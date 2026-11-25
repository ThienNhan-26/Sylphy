"""CRI-1 vertical slice orchestration.

A completed, persisted conversation turn produces one durable, truthful outcome
record and one bounded companion-state update. No autonomous action, no
scheduler, no background worker, no new persistence framework.

The slice is intentionally narrow: it runs after ConversationService has
durably persisted the turn, evaluates the deterministic InteractionPolicy
through the CRI contract wrapper, and writes one outcome row plus one bounded
state row inside a single SQLite transaction.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from sylphy.companion_contract import (
    Action,
    AutonomyDecision,
    CompanionContext,
    CompanionEvent,
    CompanionEventSource,
    CompanionEventType,
    CompanionPolicy,
    CompanionState,
    ContextItem,
    Decision,
    EvidenceKind,
    InitiativeAction,
    InitiativeDecision,
    Outcome,
    OutcomeStatus,
)
from sylphy.core import Turn
from sylphy.investigation import Investigation, InvestigationBudget, InvestigationResult, InvestigationStatus
from sylphy.interaction_policy import (
    InteractionAction,
    InteractionReason,
    PolicyConditions,
)
from sylphy.storage import SQLiteStore

logger = logging.getLogger(__name__)


def _turn_event_id(session_id: str, assistant_message_id: str) -> str:
    """Stable, bounded event id derived from the completed turn.

    Re-processing the same turn (same session + assistant message) yields the
    same event_id, so the database UNIQUE constraint enforces idempotency even
    across process restarts.
    """
    value = f"companion:turn:{session_id}:{assistant_message_id}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SliceResult:
    outcome: Outcome
    state: CompanionState
    skipped_duplicate: bool


def restore_companion_state(store: SQLiteStore) -> CompanionState:
    """Restore bounded companion state from durable storage.

    Returns the stored singleton state, or a fresh initial state when no
    companion state has been recorded yet. State is rebuilt from the
    database, never carried over from a previous Runtime instance. This
    performs no inference, no memory retrieval, no presence announcement,
    and no network access; it only reads the stored outcome/state row.
    """
    if not isinstance(store, SQLiteStore):
        raise ValueError("store must be a SQLiteStore")
    row = store.get_companion_state()
    if row is None:
        return CompanionState()
    return _row_to_companion_state(row)


def _row_to_companion_state(row) -> CompanionState:
    return CompanionState(
        revision=row[0],
        last_event_id=row[1],
        last_outcome_id=row[2],
        last_observed_at=(
            datetime.fromisoformat(row[3]) if row[3] is not None else None
        ),
        conversation_active=bool(row[4]),
        pending_presence_notices=row[5],
    )


def record_completed_turn(
    store: SQLiteStore,
    *,
    session_id: str,
    turn: Turn,
    occurred_at: datetime,
    pending_presence_notices: int = 0,
    policy: CompanionPolicy | None = None,
    conditions: PolicyConditions | None = None,
) -> SliceResult:
    """Record companion bookkeeping for a completed, persisted turn.

    Builds the CRI event/context/decision, evaluates the deterministic policy,
    persists a truthful outcome and bounded state update in one SQLite
    transaction, and returns the result.

    The conversation turn must already be persisted by ConversationService
    before this is called. Provider failure, empty response, cancellation, or
    turn-persistence failure must prevent this function from being reached.
    """
    if not isinstance(store, SQLiteStore):
        raise ValueError("store must be a SQLiteStore")
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("session_id must be a non-empty string")
    if not isinstance(turn, Turn):
        raise ValueError("turn must be a Turn")
    if not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None:
        raise ValueError("occurred_at must be a timezone-aware datetime")

    event_id = _turn_event_id(session_id, turn.assistant.id)

    # Restore durable state at the deterministic boundary: state is rebuilt
    # from the database, never carried over from a previous Runtime instance.
    base_state = restore_companion_state(store)

    event = CompanionEvent(
        event_id=event_id,
        event_type=CompanionEventType.CONVERSATION_TURN_COMPLETED,
        occurred_at=occurred_at,
        source=CompanionEventSource.CONVERSATION,
        session_id=session_id,
        correlation_id=session_id,
        payload={
            "user_message_id": turn.user.id,
            "assistant_message_id": turn.assistant.id,
        },
    )

    context = CompanionContext(
        event,
        (
            ContextItem(
                EvidenceKind.CURRENT,
                "Completed conversation turn",
                source_id=turn.assistant.id,
            ),
        ),
    )

    decision = Decision(
        event_id=event_id,
        action=InteractionAction.SPEAK,
        rationale="Assistant response was persisted for the completed turn.",
        message=turn.assistant.content,
    )

    policy = policy or CompanionPolicy()
    if conditions is None:
        conditions = PolicyConditions()
    evaluation = policy.evaluate(event, decision, conditions)

    if evaluation.allowed and evaluation.effective_action is InteractionAction.SPEAK:
        action = Action.from_evaluation(evaluation)
        outcome = Outcome.succeeded(
            action,
            evidence=turn.assistant.id,
            detail="Completed conversation turn recorded as bookkeeping.",
        )
    elif evaluation.allowed:
        # Policy allowed but selected a non-executable bookkeeping action
        # (WAIT / EXPLORE). Record the allowance truthfully without an action.
        outcome = Outcome.allowed(
            evaluation,
            detail=f"Policy allowed {evaluation.effective_action.value}; no bookkeeping action executed.",
        )
    else:
        outcome = Outcome.rejected(
            evaluation,
            detail="Policy rejected the completed-turn bookkeeping.",
        )
        logger.debug(
            "Companion policy rejected event_id=%s reason=%s",
            event_id,
            evaluation.reason_code.value,
        )

    next_state = base_state.updated(
        event,
        outcome,
        occurred_at,
        conversation_active=False,
        pending_presence_notices=pending_presence_notices,
    )

    inserted, state_row = store.record_companion_outcome(
        outcome_id=outcome.outcome_id,
        event_id=outcome.event_id,
        decision_id=outcome.decision_id,
        action_id=outcome.action_id,
        status=outcome.status.value,
        detail=outcome.detail,
        evidence=outcome.evidence,
        created_at=outcome.created_at,
        revision=next_state.revision,
        last_event_id=next_state.last_event_id,
        last_outcome_id=next_state.last_outcome_id,
        last_observed_at=next_state.last_observed_at,
        conversation_active=next_state.conversation_active,
        pending_presence_notices=next_state.pending_presence_notices,
    )

    if inserted:
        logger.debug("Companion outcome recorded event_id=%s status=%s", event_id, outcome.status.value)
        state = next_state
    else:
        logger.debug("Companion duplicate event ignored event_id=%s", event_id)
        state = _row_to_companion_state(state_row)

    return SliceResult(outcome=outcome, state=state, skipped_duplicate=not inserted)


# ---------------------------------------------------------------------------
# Presence vertical slice (Turn 6)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PresenceResult:
    """Truthful result of one bounded presence event evaluation."""

    action: InteractionAction
    reason_code: InteractionReason
    outcome: Outcome
    state: CompanionState
    context: CompanionContext
    published: bool
    skipped_duplicate: bool
    persisted: bool


def _presence_event_id(notice_id: str) -> str:
    """Stable, bounded event id for one saved presence notice.

    Re-evaluating the same saved notice (same notice id) yields the same
    event_id, so the database UNIQUE constraint prevents duplicate outcomes
    and duplicate deliveries even across restarts.
    """
    if not isinstance(notice_id, str) or not notice_id.strip():
        raise ValueError("notice_id must be a non-empty string")
    value = f"companion:presence:{notice_id.strip()}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def presence_notice_event(
    *,
    notice_id: str,
    occurred_at: datetime,
    source_session_id: str | None = None,
    expires_at: datetime | None = None,
) -> CompanionEvent:
    """Build the typed event for an already-existing, saved presence notice.

    This constructs a contract event only. It performs no retrieval, no
    inference, no scheduling, and no delivery.
    """
    return CompanionEvent(
        event_id=_presence_event_id(notice_id),
        event_type=CompanionEventType.PRESENCE_NOTICE_READY,
        occurred_at=occurred_at,
        source=CompanionEventSource.PRESENCE,
        session_id=source_session_id,
        correlation_id=notice_id,
        payload={"notice_id": notice_id},
        expires_at=expires_at,
    )


def build_presence_context(
    event: CompanionEvent,
    *,
    queue_available: bool,
    notice_source: str = "exploration",
) -> CompanionContext:
    """Assemble minimal, evidence-grounded context for a presence notice.

    The notice really exists (CONFIRMED). Delivery really happens only at an
    observed foreground boundary (CURRENT). The user's availability while the
    notice is queued is not observed (MISSING). No inference is created or
    promoted to confirmed.
    """
    if not isinstance(event, CompanionEvent):
        raise ValueError("event must be a CompanionEvent")
    if event.event_type is not CompanionEventType.PRESENCE_NOTICE_READY:
        raise ValueError("event must be a presence notice event")
    notice_id = event.payload.get("notice_id")
    if notice_id is None:
        raise ValueError("presence event must carry a notice_id")
    items = [
        ContextItem(
            EvidenceKind.CONFIRMED,
            f"Saved {notice_source} notice is pending delivery.",
            source_id=notice_id,
        ),
        ContextItem(
            EvidenceKind.CURRENT,
            "Presence delivery occurs only at an observed foreground boundary.",
        ),
        ContextItem(
            EvidenceKind.MISSING,
            "User availability while the notice is queued.",
        ),
        ContextItem(
            EvidenceKind.CURRENT,
            "Presence queue currently has capacity."
            if queue_available
            else "Presence queue is currently full.",
        ),
    ]
    return CompanionContext(event, tuple(items))


def propose_presence_decision(event: CompanionEvent, context: CompanionContext) -> Decision:
    """Deterministic proposal for an existing, eligible presence notice.

    The proposal deliberately contains no user text and no claim about the
    user's activity. Policy decides whether it may actually be delivered.
    """
    if not isinstance(event, CompanionEvent):
        raise ValueError("event must be a CompanionEvent")
    if not isinstance(context, CompanionContext):
        raise ValueError("context must be a CompanionContext")
    if context.event.event_id != event.event_id:
        raise ValueError("context event_id must match the event")
    return Decision(
        event_id=event.event_id,
        action=InteractionAction.SPEAK,
        rationale="A saved exploration question is eligible for bounded presence delivery.",
        message="Offer the saved question at the next foreground prompt boundary.",
    )


def record_presence_event(
    store: SQLiteStore,
    *,
    event: CompanionEvent,
    context: CompanionContext,
    decision: Decision,
    conditions: PolicyConditions,
    occurred_at: datetime | None = None,
    execute: Callable[[], bool] | None = None,
    pending_notice_count: Callable[[], int] | None = None,
    evidence: str | None = None,
    companion_enabled: bool = True,
    policy: CompanionPolicy | None = None,
) -> PresenceResult:
    """Evaluate one real presence event through the existing CRI pipeline.

    Pipeline: event -> context -> deterministic decision -> existing
    InteractionPolicy -> optional bounded delivery callback -> truthful
    outcome -> bounded durable state update.

    Nothing is delivered unless the existing policy allows an executable
    action. A duplicate event id is ignored before any delivery attempt.
    When the companion feature or presence feature is disabled, this function
    makes no delivery attempt and writes no companion outcome or state.
    """
    if not isinstance(store, SQLiteStore):
        raise ValueError("store must be a SQLiteStore")
    if not isinstance(event, CompanionEvent):
        raise ValueError("event must be a CompanionEvent")
    if not isinstance(context, CompanionContext):
        raise ValueError("context must be a CompanionContext")
    if not isinstance(decision, Decision):
        raise ValueError("decision must be a Decision")
    if not isinstance(conditions, PolicyConditions):
        raise ValueError("conditions must be PolicyConditions")
    if context.event.event_id != event.event_id:
        raise ValueError("context event_id must match the event")
    if decision.event_id != event.event_id:
        raise ValueError("decision event_id must match the event")
    if occurred_at is not None and (
        not isinstance(occurred_at, datetime) or occurred_at.tzinfo is None
    ):
        raise ValueError("occurred_at must be a timezone-aware datetime")
    if execute is not None and not callable(execute):
        raise ValueError("execute must be callable")
    if pending_notice_count is not None and not callable(pending_notice_count):
        raise ValueError("pending_notice_count must be callable")

    policy = policy or CompanionPolicy()
    base_state = restore_companion_state(store)
    observed_at = occurred_at or datetime.now(timezone.utc)

    logger.debug(
        "Companion presence event received event_id=%s notice_id=%s",
        event.event_id,
        event.payload.get("notice_id"),
    )

    # Feature disabled is not a silent delivery with hidden bookkeeping: the
    # presence loop is inactive and writes nothing.
    if not companion_enabled:
        conditions = replace(conditions, feature_enabled=False)
        evaluation = policy.evaluate(event, decision, conditions)
        outcome = (
            Outcome.allowed(evaluation, detail="Companion feature disabled; presence inactive.")
            if evaluation.allowed
            else Outcome.rejected(evaluation, detail="Companion feature disabled; presence inactive.")
        )
        logger.debug(
            "Companion presence inactive event_id=%s reason=%s",
            event.event_id,
            evaluation.reason_code.value,
        )
        return PresenceResult(
            action=evaluation.effective_action,
            reason_code=evaluation.reason_code,
            outcome=outcome,
            state=base_state,
            context=context,
            published=False,
            skipped_duplicate=False,
            persisted=False,
        )

    # Database-enforced idempotency: never deliver or record the same event
    # twice, even if upstream eligibility checks were bypassed.
    existing = store.get_companion_outcome(event.event_id)
    if existing is not None:
        conditions = replace(conditions, duplicate=True)
        evaluation = policy.evaluate(event, decision, conditions)
        outcome = (
            Outcome.allowed(evaluation, detail="Duplicate presence event ignored.")
            if evaluation.allowed
            else Outcome.rejected(evaluation, detail="Duplicate presence event ignored.")
        )
        logger.debug(
            "Companion presence duplicate ignored event_id=%s reason=%s",
            event.event_id,
            evaluation.reason_code.value,
        )
        return PresenceResult(
            action=evaluation.effective_action,
            reason_code=evaluation.reason_code,
            outcome=outcome,
            state=base_state,
            context=context,
            published=False,
            skipped_duplicate=True,
            persisted=False,
        )

    evaluation = policy.evaluate(event, decision, conditions)
    logger.debug(
        "Companion presence decision event_id=%s action=%s reason=%s allowed=%s",
        event.event_id,
        decision.action.value,
        evaluation.reason_code.value,
        evaluation.allowed,
    )

    published = False
    if evaluation.allowed and evaluation.effective_action is InteractionAction.SPEAK:
        if execute is None:
            outcome = Outcome.allowed(
                evaluation,
                detail="Policy allowed presence delivery; no delivery callback was provided.",
            )
        else:
            action = Action.from_evaluation(evaluation)
            try:
                published = bool(execute())
            except Exception:
                logger.exception(
                    "Companion presence delivery raised; recording failure event_id=%s",
                    event.event_id,
                )
                published = False
            if published:
                outcome = Outcome.succeeded(
                    action,
                    evidence=evidence or event.event_id,
                    detail="Presence notice queued for delivery at an observed foreground boundary.",
                )
            else:
                outcome = Outcome.failed(
                    action,
                    evidence=evidence or "presence_delivery_not_confirmed",
                    detail="Presence notice was not delivered.",
                )
    elif evaluation.allowed:
        outcome = Outcome.allowed(
            evaluation,
            detail=(
                f"Policy allowed {evaluation.effective_action.value}; "
                "no presence action was executed."
            ),
        )
    else:
        outcome = Outcome.rejected(
            evaluation,
            detail=f"Policy rejected presence delivery: {evaluation.reason_code.value}.",
        )
        logger.debug(
            "Companion presence silenced event_id=%s reason=%s",
            event.event_id,
            evaluation.reason_code.value,
        )

    if base_state.last_observed_at is not None and observed_at < base_state.last_observed_at:
        # Clock skew or out-of-order events must not move durable observation
        # time backwards; the event itself keeps its original occurred_at.
        observed_at = base_state.last_observed_at

    pending = (
        pending_notice_count()
        if pending_notice_count is not None
        else base_state.pending_presence_notices
    )
    next_state = base_state.updated(
        event,
        outcome,
        observed_at,
        conversation_active=False,
        pending_presence_notices=pending,
    )

    inserted, state_row = store.record_companion_outcome(
        outcome_id=outcome.outcome_id,
        event_id=outcome.event_id,
        decision_id=outcome.decision_id,
        action_id=outcome.action_id,
        status=outcome.status.value,
        detail=outcome.detail,
        evidence=outcome.evidence,
        created_at=outcome.created_at,
        revision=next_state.revision,
        last_event_id=next_state.last_event_id,
        last_outcome_id=next_state.last_outcome_id,
        last_observed_at=next_state.last_observed_at,
        conversation_active=next_state.conversation_active,
        pending_presence_notices=next_state.pending_presence_notices,
    )

    if inserted:
        logger.debug(
            "Companion presence outcome recorded event_id=%s status=%s published=%s",
            event.event_id,
            outcome.status.value,
            published,
        )
        state = next_state
    else:
        # A concurrent writer recorded this event first; apply no further
        # state revision and report the stored state.
        logger.debug(
            "Companion presence duplicate commit ignored event_id=%s",
            event.event_id,
        )
        state = _row_to_companion_state(state_row)

    return PresenceResult(
        action=evaluation.effective_action,
        reason_code=evaluation.reason_code,
        outcome=outcome,
        state=state,
        context=context,
        published=published,
        skipped_duplicate=not inserted,
        persisted=inserted,
    )


# ---------------------------------------------------------------------------
# Bounded autonomy vertical slice (Turn 7)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutonomyResult:
    """Truthful result of one bounded autonomous action evaluation."""

    decision: AutonomyDecision
    outcome: Outcome
    action: Action | None
    state: CompanionState
    executed: bool
    skipped_duplicate: bool
    persisted: bool


def _autonomy_decision_id(trigger_event_id: str, action_category: str, action: InteractionAction) -> str:
    """Stable, bounded decision id derived from the trigger event and proposal."""
    value = f"companion:autonomy:{trigger_event_id}:{action_category}:{action.value}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _autonomy_scope(scope: Mapping[str, str] | None) -> dict[str, str]:
    if scope:
        return {str(key): str(value) for key, value in scope.items()}
    return {str(key): str(value) for key, value in InvestigationBudget().as_dict().items()}


def propose_autonomy_decision(
    event: CompanionEvent,
    context: CompanionContext,
    *,
    conditions: PolicyConditions,
    action_category: str = "investigation",
    scope: Mapping[str, str] | None = None,
    required_permissions: tuple[str, ...] = ("investigation_search",),
) -> AutonomyDecision:
    """Deterministically propose one bounded autonomous action for a real trigger.

    The proposal is a deterministic function of the event, the context, and the
    application-verified conditions. No LLM is involved. Silence is proposed when
    the autonomy feature is disabled, permission is denied, the trigger is a
    duplicate, the trigger expired, or the evidence is insufficient. A permission
    request is proposed when permission has not been granted yet. Otherwise one
    bounded action (EXPLORE of the existing investigation capability) is proposed.
    """
    conditions = conditions or PolicyConditions()
    now = datetime.now(timezone.utc)
    event_id = event.event_id
    evidence = tuple(item.source_id for item in context.items if item.source_id is not None)
    bounded_scope = _autonomy_scope(scope)

    def proposed(action: InteractionAction, why: str, permissions: tuple[str, ...] = required_permissions,
                 when: datetime | None = None) -> AutonomyDecision:
        return AutonomyDecision(
            decision_id=_autonomy_decision_id(event_id, action_category, action),
            trigger_event_id=event_id,
            proposed_action=action,
            action_category=action_category,
            scope=bounded_scope,
            required_permissions=permissions,
            evidence=evidence,
            reason=why,
            expires_at=when,
        )

    if not conditions.feature_enabled:
        return proposed(InteractionAction.SILENCE, "Autonomy feature is disabled; no autonomous action was proposed.")
    if conditions.permission_denied:
        return proposed(InteractionAction.SILENCE, "Permission denied; the autonomous action was refused.")
    if conditions.duplicate:
        return proposed(InteractionAction.SILENCE, "Duplicate autonomy trigger; no second autonomous action was proposed.")
    if conditions.expired or (event.expires_at is not None and event.expires_at <= now):
        return proposed(InteractionAction.SILENCE, "The autonomous trigger expired before evaluation.")
    if not conditions.grounded or not context.confirmed:
        return proposed(InteractionAction.SILENCE, "Insufficient evidence; the autonomous action was refused.")
    if conditions.request_permission and not conditions.permission_granted:
        return proposed(
            InteractionAction.REQUEST_PERMISSION,
            "The autonomous action requires an explicit permission grant before execution.",
            permissions=required_permissions,
        )

    action = InteractionAction.EXPLORE
    duration = int(bounded_scope.get("max_duration_seconds") or InvestigationBudget().max_duration_seconds)
    return proposed(
        action,
        "A delivered saved exploration question is eligible for one bounded autonomous investigation.",
        when=now + timedelta(seconds=max(1, duration)),
    )


def record_autonomy_execution(
    store: SQLiteStore,
    *,
    decision: AutonomyDecision,
    context: CompanionContext,
    conditions: PolicyConditions,
    execute: Callable[[Action], Investigation] | None = None,
    pending_notice_count: Callable[[], int] | None = None,
    companion_enabled: bool = True,
    policy: CompanionPolicy | None = None,
) -> AutonomyResult:
    """Evaluate one real autonomous trigger and commit a truthful outcome.

    Pipeline: event -> context -> deterministic autonomy decision -> existing
    InteractionPolicy -> permission gate -> bounded execution -> truthful outcome
    -> bounded durable state update.

    No execution happens unless policy permits an executable action and the
    permission gate allows it. A duplicate decision id is ignored before any
    execution attempt.
    """
    if not isinstance(store, SQLiteStore):
        raise ValueError("store must be a SQLiteStore")
    if not isinstance(decision, AutonomyDecision):
        raise ValueError("decision must be an AutonomyDecision")
    if not isinstance(context, CompanionContext):
        raise ValueError("context must be a CompanionContext")
    if not isinstance(conditions, PolicyConditions):
        raise ValueError("conditions must be PolicyConditions")
    if context.event.event_id != decision.trigger_event_id:
        raise ValueError("context event_id must match the autonomy trigger event")

    policy = policy or CompanionPolicy()
    base_state = restore_companion_state(store)
    observed_at = datetime.now(timezone.utc)

    event = CompanionEvent(
        event_id=decision.decision_id,
        event_type=CompanionEventType.INVESTIGATION_COMPLETED,
        occurred_at=observed_at,
        source=CompanionEventSource.INVESTIGATION,
        correlation_id=decision.decision_id,
        payload={"decision_id": decision.decision_id},
    )
    policy_decision = Decision(
        event_id=decision.decision_id,
        decision_id=decision.decision_id,
        action=decision.proposed_action,
        rationale=decision.reason,
        message=None,
        permission_scope=(
            decision.required_permissions[0]
            if decision.proposed_action is InteractionAction.REQUEST_PERMISSION
            else None
        ),
    )
    conditions = replace(conditions, requested_action=decision.proposed_action)

    if not companion_enabled:
        evaluation = policy.evaluate(event, policy_decision, conditions)
        outcome = (
            Outcome.allowed(evaluation, detail="Companion feature disabled; autonomous action inactive.")
            if evaluation.allowed
            else Outcome.rejected(evaluation, detail="Companion feature disabled; autonomous action inactive.")
        )
        logger.debug(
            "Companion autonomy inactive decision_id=%s reason=%s",
            decision.decision_id, evaluation.reason_code.value,
        )
        return AutonomyResult(
            decision=decision, outcome=outcome, action=None, state=base_state,
            executed=False, skipped_duplicate=False, persisted=False,
        )

    existing = store.get_companion_outcome(decision.decision_id)
    if existing is not None:
        conditions = replace(conditions, duplicate=True)
        evaluation = policy.evaluate(event, policy_decision, conditions)
        outcome = (
            Outcome.allowed(evaluation, detail="Duplicate autonomy action ignored.")
            if evaluation.allowed
            else Outcome.rejected(evaluation, detail="Duplicate autonomy action ignored.")
        )
        logger.debug(
            "Companion autonomy duplicate ignored decision_id=%s reason=%s",
            decision.decision_id, evaluation.reason_code.value,
        )
        return AutonomyResult(
            decision=decision, outcome=outcome, action=None, state=base_state,
            executed=False, skipped_duplicate=True, persisted=False,
        )

    evaluation = policy.evaluate(event, policy_decision, conditions)
    executed = False
    action: Action | None = None
    if not evaluation.allowed:
        outcome = Outcome.rejected(
            evaluation, detail=f"Autonomous action rejected: {evaluation.reason_code.value}."
        )
    elif evaluation.effective_action is InteractionAction.SILENCE:
        outcome = Outcome.allowed(
            evaluation, detail=f"Autonomous action silenced: {evaluation.reason_code.value}."
        )
    elif evaluation.effective_action is InteractionAction.REQUEST_PERMISSION:
        outcome = Outcome.allowed(
            evaluation, detail="Autonomous action requires permission; no execution was performed."
        )
    elif evaluation.effective_action is InteractionAction.SPEAK:
        outcome = Outcome.allowed(
            evaluation, detail="Autonomous action allowed without an executable effect."
        )
    elif evaluation.effective_action is InteractionAction.EXPLORE and execute is not None:
        question_id = decision.evidence[0] if decision.evidence else None
        if question_id is None:
            outcome = Outcome.allowed(
                evaluation, detail="Autonomous action allowed without an investigation target."
            )
        else:
            action = Action.from_evaluation(evaluation, parameters={"question_id": question_id})
            executed = True
            try:
                investigation = execute(action)
                if investigation.status is InvestigationStatus.COMPLETED:
                    outcome = Outcome.succeeded(
                        action, evidence=investigation.id,
                        detail="Bounded autonomous investigation completed.",
                    )
                elif investigation.status is InvestigationStatus.CANCELLED:
                    outcome = Outcome.cancelled(action, detail="Bounded autonomous investigation was cancelled.")
                elif investigation.status is InvestigationStatus.FAILED:
                    outcome = Outcome.failed(
                        action, evidence=investigation.id,
                        detail=investigation.last_error or "Bounded autonomous investigation failed.",
                    )
                elif investigation.status is InvestigationStatus.EXPIRED:
                    outcome = Outcome.failed(
                        action, evidence=investigation.id,
                        detail="Bounded autonomous investigation expired.",
                    )
                else:
                    outcome = Outcome.failed(
                        action, evidence=investigation.id,
                        detail="Bounded autonomous investigation did not reach a terminal state.",
                    )
            except Exception as exc:
                outcome = Outcome.failed(
                    action, evidence=action.action_id, detail=f"Autonomous execution failed ({type(exc).__name__})."
                )
    else:
        outcome = Outcome.allowed(
            evaluation, detail=f"Autonomous action permitted {evaluation.effective_action.value} without execution."
        )

    next_state = base_state.updated(
        event,
        outcome,
        observed_at,
        conversation_active=False,
        pending_presence_notices=(
            pending_notice_count() if pending_notice_count is not None else base_state.pending_presence_notices
        ),
    )

    inserted, state_row = store.record_companion_outcome(
        outcome_id=outcome.outcome_id,
        event_id=outcome.event_id,
        decision_id=outcome.decision_id,
        action_id=outcome.action_id,
        status=outcome.status.value,
        detail=outcome.detail,
        evidence=outcome.evidence,
        created_at=outcome.created_at,
        revision=next_state.revision,
        last_event_id=next_state.last_event_id,
        last_outcome_id=next_state.last_outcome_id,
        last_observed_at=next_state.last_observed_at,
        conversation_active=next_state.conversation_active,
        pending_presence_notices=next_state.pending_presence_notices,
    )

    state = next_state if inserted else _row_to_companion_state(state_row)

    return AutonomyResult(
        decision=decision,
        outcome=outcome,
        action=action,
        state=state,
        executed=executed,
        skipped_duplicate=False,
        persisted=inserted,
    )


# ---------------------------------------------------------------------------
# Bounded purposeful-initiative vertical slice (Turn 8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InitiativeResult:
    """Truthful result of one bounded initiative evaluation."""

    decision: InitiativeDecision
    outcome: Outcome
    action: Action | None
    state: CompanionState
    delivered: bool
    skipped_duplicate: bool
    persisted: bool


def _initiative_decision_id(trigger_event_id: str, action_category: str, action_value: str) -> str:
    """Stable, bounded decision id derived from the trigger event and proposal."""
    value = f"companion:initiative:{trigger_event_id}:{action_category}:{action_value}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def initiative_trigger_event(
    investigation_id: str,
    *,
    occurred_at: datetime,
    expires_at: datetime | None = None,
) -> CompanionEvent:
    """Build the typed trigger event for one bounded investigation completion.

    The event id is a deterministic hash of the investigation id, so re-evaluating
    the same completion is idempotent. The event performs no retrieval, inference,
    or delivery by itself.
    """
    if not isinstance(investigation_id, str) or not investigation_id.strip():
        raise ValueError("investigation_id must be a non-empty string")
    clean_id = investigation_id.strip()
    if len(clean_id) > 96 or not re.fullmatch(r"[A-Za-z0-9_.-]+", clean_id):
        raise ValueError("investigation_id must be a short opaque identifier")
    event_id = hashlib.sha256(f"companion:initiative:{clean_id}".encode("utf-8")).hexdigest()
    return CompanionEvent(
        event_id=event_id,
        event_type=CompanionEventType.INVESTIGATION_COMPLETED,
        occurred_at=occurred_at,
        source=CompanionEventSource.INVESTIGATION,
        correlation_id=clean_id,
        payload={"investigation_id": clean_id},
        expires_at=expires_at,
    )


def build_initiative_context(
    event: CompanionEvent,
    *,
    investigation_id: str,
    result: InvestigationResult | None,
) -> CompanionContext:
    """Assemble minimal, evidence-grounded context for one investigation completion."""
    if not isinstance(event, CompanionEvent):
        raise ValueError("event must be a CompanionEvent")
    if event.event_type is not CompanionEventType.INVESTIGATION_COMPLETED:
        raise ValueError("event must be an investigation completion event")
    clean_id = investigation_id.strip() if isinstance(investigation_id, str) else ""
    if not clean_id or len(clean_id) > 96 or not re.fullmatch(r"[A-Za-z0-9_.-]+", clean_id):
        raise ValueError("investigation_id must be a short opaque identifier")
    items: list[ContextItem] = [
        ContextItem(
            EvidenceKind.CONFIRMED,
            "The bounded investigation has a persisted terminal result.",
            source_id=clean_id,
        ),
    ]
    if result is not None:
        items.append(
            ContextItem(
                EvidenceKind.CURRENT,
                f"Investigation result evidence_sufficient={str(bool(result.evidence_sufficient)).lower()}.",
                source_id=clean_id,
            )
        )
    else:
        items.append(
            ContextItem(
                EvidenceKind.MISSING,
                "No investigation result was persisted for this trigger.",
                source_id=None,
            )
        )
    items.append(
        ContextItem(
            EvidenceKind.MISSING,
            "Whether the user can receive an initiative response now.",
            source_id=None,
        )
    )
    return CompanionContext(event, tuple(items))


def _initiative_interaction_action(decision: InitiativeDecision) -> InteractionAction:
    """Map one bounded initiative proposal onto the existing policy action space.

    COMMUNICATION is represented by the existing SPEAK action (the only
    executable user-facing delivery action); REJECT proposals carry the refusal
    so the deterministic policy can resolve them authoritatively.
    """
    if decision.proposed_action is InitiativeAction.COMMUNICATE:
        return InteractionAction.SPEAK
    if decision.proposed_action is InitiativeAction.WAIT:
        return InteractionAction.WAIT
    if decision.proposed_action is InitiativeAction.SILENCE:
        return InteractionAction.SILENCE
    if decision.proposed_action is InitiativeAction.REQUEST_PERMISSION:
        return InteractionAction.REQUEST_PERMISSION
    return InteractionAction.SPEAK


def propose_initiative_decision(
    event: CompanionEvent,
    context: CompanionContext,
    *,
    conditions: PolicyConditions,
    result: InvestigationResult | None = None,
    response: str | None = None,
) -> InitiativeDecision:
    """Deterministically propose whether one completed investigation warrants an initiative.

    The proposal is a deterministic function of the event, the context, the
    verified conditions, and the bounded investigation result. No LLM is
    involved. Silence is proposed when the initiative feature is disabled, the
    trigger is a duplicate, or the result is not supported by sufficient
    evidence. A permission request is proposed when delivery permission has not
    been granted. A refusal (REJECT) is proposed when delivery permission is
    denied. Otherwise COMMUNICATION is proposed for one bounded response. WAIT
    defers delivery when cooldown conditions require it.
    """
    conditions = conditions or PolicyConditions()
    now = datetime.now(timezone.utc)
    event_id = event.event_id
    evidence = tuple(item.source_id for item in context.items if item.source_id is not None)
    evidence_kinds = tuple(item.kind for item in context.items if item.source_id is not None)

    def proposed(
        action: InitiativeAction,
        why: str,
        *,
        when: datetime | None = None,
        response_text: str | None = None,
        permissions: tuple[str, ...] = (),
    ) -> InitiativeDecision:
        action_value = action.value if isinstance(action, InitiativeAction) else str(action)
        return InitiativeDecision(
            decision_id=_initiative_decision_id(event_id, "initiative", action_value),
            trigger_event_id=event_id,
            source=event.source,
            proposed_action=action,
            action_category="initiative",
            evidence=evidence,
            evidence_kinds=evidence_kinds,
            response=response_text,
            required_permissions=permissions,
            reason=why,
            expires_at=when or event.expires_at,
        )

    if not conditions.feature_enabled:
        return proposed(
            InitiativeAction.SILENCE,
            "Initiative feature is disabled; no initiative was proposed.",
            response_text=response,
        )
    if conditions.duplicate:
        return proposed(
            InitiativeAction.SILENCE,
            "Duplicate initiative trigger; no second initiative was proposed.",
            response_text=response,
        )
    warranted = result is not None and bool(result.evidence_sufficient)
    bounded_response = response if response and response.strip() else (
        result.summary.strip() if result is not None else None
    )
    if not warranted:
        return proposed(
            InitiativeAction.SILENCE,
            "The bounded investigation did not produce sufficient evidence; no initiative was proposed.",
            response_text=bounded_response,
        )
    if conditions.permission_denied:
        return proposed(
            InitiativeAction.REJECT,
            "Permission denied for autonomous communication delivery; the initiative was refused.",
            response_text=bounded_response,
            permissions=(),
        )
    if conditions.permission_granted is False or conditions.request_permission:
        return proposed(
            InitiativeAction.REQUEST_PERMISSION,
            "Autonomous communication delivery requires an explicit permission grant before delivery.",
            response_text=bounded_response,
            permissions=("companion_deliver",),
        )
    if conditions.cooldown_active:
        if conditions.can_wait:
            return proposed(
                InitiativeAction.WAIT,
                "Delivery deferred by cooldown; the initiative may proceed later.",
                when=conditions.not_before,
                response_text=bounded_response,
            )
        return proposed(
            InitiativeAction.SILENCE,
            "Delivery deferred by cooldown; no initiative was proposed.",
            response_text=bounded_response,
        )
    if not conditions.queue_available:
        return proposed(
            InitiativeAction.SILENCE,
            "Delivery queue is currently full; the initiative was not proposed.",
            response_text=bounded_response,
        )
    return proposed(
        InitiativeAction.COMMUNICATE,
        "The bounded investigation produced sufficient evidence; one bounded initiative response was proposed.",
        response_text=bounded_response,
        permissions=("companion_deliver",),
    )


def record_initiative(
    store: SQLiteStore,
    *,
    decision: InitiativeDecision,
    context: CompanionContext,
    conditions: PolicyConditions,
    execute: Callable[[Action], bool] | None = None,
    pending_notice_count: Callable[[], int] | None = None,
    companion_enabled: bool = True,
    policy: CompanionPolicy | None = None,
) -> InitiativeResult:
    """Evaluate one real initiative trigger and commit a truthful outcome.

    Pipeline: event -> context -> deterministic initiative decision -> existing
    InteractionPolicy -> permission gate -> bounded delivery -> truthful outcome
    -> bounded durable state update.

    No delivery happens unless the existing policy permits an executable
    communication action and the permission gate allows it. A duplicate decision
    id is ignored before any delivery attempt.
    """
    if not isinstance(store, SQLiteStore):
        raise ValueError("store must be a SQLiteStore")
    if not isinstance(decision, InitiativeDecision):
        raise ValueError("decision must be an InitiativeDecision")
    if not isinstance(context, CompanionContext):
        raise ValueError("context must be a CompanionContext")
    if not isinstance(conditions, PolicyConditions):
        raise ValueError("conditions must be PolicyConditions")
    if context.event.event_id != decision.trigger_event_id:
        raise ValueError("context event_id must match the initiative trigger event")

    policy = policy or CompanionPolicy()
    base_state = restore_companion_state(store)
    observed_at = datetime.now(timezone.utc)

    event = CompanionEvent(
        event_id=decision.trigger_event_id,
        event_type=CompanionEventType.INVESTIGATION_COMPLETED,
        occurred_at=observed_at,
        source=decision.source,
        correlation_id=decision.trigger_event_id,
        payload={"decision_id": decision.decision_id},
        expires_at=decision.expires_at,
    )
    interaction_action = _initiative_interaction_action(decision)
    policy_decision = Decision(
        event_id=decision.trigger_event_id,
        decision_id=decision.decision_id,
        action=interaction_action,
        rationale=decision.reason,
        message=(
            decision.response or decision.reason
            if interaction_action is InteractionAction.SPEAK
            else None
        ),
        permission_scope=(
            decision.required_permissions[0]
            if interaction_action is InteractionAction.REQUEST_PERMISSION
            else None
        ),
    )

    if not companion_enabled:
        evaluation = policy.evaluate(event, policy_decision, conditions)
        outcome = (
            Outcome.allowed(evaluation, detail="Companion feature disabled; initiative inactive.")
            if evaluation.allowed
            else Outcome.rejected(evaluation, detail="Companion feature disabled; initiative inactive.")
        )
        logger.debug(
            "Companion initiative inactive decision_id=%s reason=%s",
            decision.decision_id, evaluation.reason_code.value,
        )
        return InitiativeResult(
            decision=decision, outcome=outcome, action=None, state=base_state,
            delivered=False, skipped_duplicate=False, persisted=False,
        )

    existing = store.get_companion_outcome(decision.trigger_event_id)
    if existing is not None:
        conditions = replace(conditions, duplicate=True)
        evaluation = policy.evaluate(event, policy_decision, conditions)
        outcome = (
            Outcome.allowed(evaluation, detail="Duplicate initiative ignored.")
            if evaluation.allowed
            else Outcome.rejected(evaluation, detail="Duplicate initiative ignored.")
        )
        logger.debug(
            "Companion initiative duplicate ignored decision_id=%s reason=%s",
            decision.decision_id, evaluation.reason_code.value,
        )
        return InitiativeResult(
            decision=decision, outcome=outcome, action=None, state=base_state,
            delivered=False, skipped_duplicate=True, persisted=False,
        )

    evaluation = policy.evaluate(event, policy_decision, conditions)
    delivered = False
    action: Action | None = None
    if not evaluation.allowed:
        outcome = Outcome.rejected(
            evaluation, detail=f"Initiative rejected: {evaluation.reason_code.value}."
        )
    elif evaluation.effective_action is InteractionAction.SILENCE:
        outcome = Outcome.allowed(
            evaluation, detail=f"Initiative silenced: {evaluation.reason_code.value}."
        )
    elif evaluation.effective_action is InteractionAction.WAIT:
        outcome = Outcome.allowed(
            evaluation, detail=f"Initiative deferred: {evaluation.reason_code.value}."
        )
    elif evaluation.effective_action is InteractionAction.REQUEST_PERMISSION:
        outcome = Outcome.allowed(
            evaluation, detail="Initiative requires permission; no delivery was performed."
        )
    elif evaluation.effective_action is InteractionAction.SPEAK:
        if execute is None:
            outcome = Outcome.allowed(
                evaluation, detail="Policy allowed initiative delivery; no delivery callback was provided."
            )
        else:
            action = Action.from_evaluation(
                evaluation,
                parameters={
                    "investigation_id": decision.evidence[0] if decision.evidence else decision.decision_id
                },
            )
            try:
                delivered = bool(execute(action))
            except Exception:
                delivered = False
                outcome = Outcome.failed(
                    action, evidence=action.action_id, detail="Bounded initiative delivery failed."
                )
            if delivered:
                outcome = Outcome.succeeded(
                    action, evidence=action.action_id, detail="Bounded initiative response queued for presence delivery."
                )
            else:
                outcome = Outcome.failed(
                    action, evidence=action.action_id, detail="Bounded initiative response was not delivered."
                )
    else:
        outcome = Outcome.allowed(
            evaluation, detail=f"Initiative permitted {evaluation.effective_action.value} without delivery."
        )

    next_state = base_state.updated(
        event,
        outcome,
        observed_at,
        conversation_active=False,
        pending_presence_notices=(
            pending_notice_count() if pending_notice_count is not None else base_state.pending_presence_notices
        ),
    )

    inserted, state_row = store.record_companion_outcome(
        outcome_id=outcome.outcome_id,
        event_id=outcome.event_id,
        decision_id=outcome.decision_id,
        action_id=outcome.action_id,
        status=outcome.status.value,
        detail=outcome.detail,
        evidence=outcome.evidence,
        created_at=outcome.created_at,
        revision=next_state.revision,
        last_event_id=next_state.last_event_id,
        last_outcome_id=next_state.last_outcome_id,
        last_observed_at=next_state.last_observed_at,
        conversation_active=next_state.conversation_active,
        pending_presence_notices=next_state.pending_presence_notices,
    )

    state = next_state if inserted else _row_to_companion_state(state_row)

    return InitiativeResult(
        decision=decision,
        outcome=outcome,
        action=action,
        state=state,
        delivered=delivered,
        skipped_duplicate=not inserted,
        persisted=inserted,
    )


__all__ = [
    "AutonomyResult",
    "InitiativeResult",
    "PresenceResult",
    "SliceResult",
    "build_initiative_context",
    "build_presence_context",
    "initiative_trigger_event",
    "propose_autonomy_decision",
    "propose_initiative_decision",
    "propose_presence_decision",
    "record_autonomy_execution",
    "record_completed_turn",
    "record_initiative",
    "record_presence_event",
    "restore_companion_state",
]
