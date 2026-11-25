from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from sylphy import companion_contract as contract_module
from sylphy.companion_contract import (
    Action,
    CompanionContext,
    CompanionEvent,
    CompanionEventSource,
    CompanionEventType,
    CompanionPolicy,
    CompanionState,
    ContextItem,
    Decision,
    EvidenceKind,
    Outcome,
    OutcomeStatus,
    PolicyEvaluation,
)
from sylphy.identity import load_identity
from sylphy.interaction_policy import InteractionAction, InteractionReason, PolicyConditions


NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def make_event(**overrides):
    data = {
        "event_id": "event-1",
        "event_type": CompanionEventType.CONVERSATION_TURN_COMPLETED,
        "occurred_at": NOW,
        "source": CompanionEventSource.CONVERSATION,
        "session_id": "session-1",
        "correlation_id": "turn-1",
        "payload": {"turn_id": "turn-1"},
    }
    data.update(overrides)
    return CompanionEvent(**data)


def make_speak_decision(**overrides):
    data = {
        "event_id": "event-1",
        "action": InteractionAction.SPEAK,
        "rationale": "Respond to the user.",
        "message": "Hello.",
    }
    data.update(overrides)
    return Decision(**data)


def allowed_evaluation():
    return CompanionPolicy().evaluate(make_event(), make_speak_decision())


def denied_evaluation():
    return CompanionPolicy().evaluate(
        make_event(), make_speak_decision(), PolicyConditions(permission_denied=True)
    )


# Event ---------------------------------------------------------------------


def test_event_constructs_and_serializes_stably():
    event = make_event()
    payload = event.to_dict()

    assert payload["event_id"] == "event-1"
    assert payload["event_type"] == "conversation_turn_completed"
    assert payload["source"] == "conversation"
    assert payload["payload"] == {"turn_id": "turn-1"}
    assert json.loads(event.to_json()) == payload
    assert event.to_json() == event.to_json()

    with pytest.raises(TypeError):
        event.payload["turn_id"] = "other"


@pytest.mark.parametrize(
    "overrides,message",
    [
        ({"event_id": ""}, "event_id"),
        ({"event_id": "bad id"}, "event_id"),
        ({"event_type": "conversation_turn_completed"}, "event_type"),
        ({"occurred_at": datetime(2026, 11, 22, 12, 0)}, "timezone-aware"),
        ({"source": "conversation"}, "source"),
        ({"session_id": "bad id"}, "session_id"),
        ({"payload": {"bad key": "value"}}, "payload keys"),
        ({"payload": {"turn_id": 1}}, "payload values"),
        ({"payload": {"turn_id": ""}}, "payload values"),
        ({"expires_at": datetime(2026, 11, 22, 12, 0)}, "timezone-aware"),
    ],
)
def test_event_rejects_invalid_fields(overrides, message):
    with pytest.raises(ValueError, match=message):
        make_event(**overrides)


def test_event_requires_identifier():
    with pytest.raises(TypeError):
        CompanionEvent(
            event_type=CompanionEventType.CONVERSATION_TURN_COMPLETED,
            occurred_at=NOW,
            source=CompanionEventSource.CONVERSATION,
        )


# Context -------------------------------------------------------------------


def test_context_distinguishes_confirmed_inferred_and_missing():
    context = CompanionContext(
        make_event(),
        (
            ContextItem(EvidenceKind.CONFIRMED, "The project is Cedar.", source_id="memory-1"),
            ContextItem(EvidenceKind.CURRENT, "The user asked about Cedar.", source_id="message-1"),
            ContextItem(EvidenceKind.INFERRED, "Cedar may be a project name.", confidence=0.7),
            ContextItem(EvidenceKind.MISSING, "No stored deadline is available."),
        ),
    )

    assert [item.content for item in context.confirmed] == ["The project is Cedar."]
    assert [item.content for item in context.inferred] == ["Cedar may be a project name."]
    assert context.inferred[0] not in context.confirmed
    assert context.missing[0].kind is EvidenceKind.MISSING
    assert context.has_missing is True


def test_context_rejects_inference_without_confidence_and_confirmed_without_source():
    with pytest.raises(ValueError, match="confirmed context requires a source_id"):
        ContextItem(EvidenceKind.CONFIRMED, "A fact.")
    with pytest.raises(ValueError, match="inferred context requires a confidence"):
        ContextItem(EvidenceKind.INFERRED, "A guess.")
    with pytest.raises(ValueError, match="missing context cannot claim"):
        ContextItem(EvidenceKind.MISSING, "Unknown.", source_id="memory-1")
    with pytest.raises(ValueError, match="current interaction context"):
        ContextItem(EvidenceKind.CURRENT, "Current.", confidence=0.5)
    with pytest.raises(ValueError, match="between 0 and 1"):
        ContextItem(EvidenceKind.INFERRED, "A guess.", confidence=1.5)


def test_context_rejects_non_context_items():
    with pytest.raises(ValueError, match="ContextItem"):
        CompanionContext(make_event(), ("not an item",))


# Decision ------------------------------------------------------------------


def test_decision_supports_bounded_actions():
    speak = make_speak_decision()
    wait = Decision("event-1", InteractionAction.WAIT, "Wait.", not_before=NOW + timedelta(minutes=1))
    silence = Decision("event-1", InteractionAction.SILENCE, "No response.")
    request = Decision(
        "event-1", InteractionAction.REQUEST_PERMISSION, "Ask.", permission_scope="camera"
    )
    explore = Decision("event-1", InteractionAction.EXPLORE, "Explore.")

    assert speak.action is InteractionAction.SPEAK
    assert wait.not_before == NOW + timedelta(minutes=1)
    assert silence.action is InteractionAction.SILENCE
    assert request.permission_scope == "camera"
    assert explore.action is InteractionAction.EXPLORE


def test_decision_rejects_invalid_shapes():
    with pytest.raises(ValueError, match="SPEAK decisions require a message"):
        Decision("event-1", InteractionAction.SPEAK, "Respond.")
    with pytest.raises(ValueError, match="Only SPEAK"):
        Decision("event-1", InteractionAction.WAIT, "Wait.", message="Hello.")
    with pytest.raises(ValueError, match="permission_scope"):
        Decision("event-1", InteractionAction.REQUEST_PERMISSION, "Ask.")
    with pytest.raises(ValueError, match="Only REQUEST_PERMISSION"):
        Decision(
            "event-1", InteractionAction.SPEAK, "Respond.", message="Hello.",
            permission_scope="camera",
        )
    with pytest.raises(ValueError, match="Only WAIT"):
        Decision(
            "event-1", InteractionAction.SPEAK, "Respond.", message="Hello.", not_before=NOW
        )
    with pytest.raises(ValueError, match="action must be"):
        Decision("event-1", "SPEAK", "Respond.")


# Policy --------------------------------------------------------------------


def test_policy_allows_speak_and_preserves_reason():
    event = make_event()
    decision = make_speak_decision()
    evaluation = CompanionPolicy().evaluate(event, decision)

    assert evaluation.allowed is True
    assert evaluation.effective_action is InteractionAction.SPEAK
    assert evaluation.reason_code is InteractionReason.ELIGIBLE_TO_SHARE
    assert evaluation.policy_decision.source_event_id == event.event_id
    assert evaluation.policy_decision.message == "Hello."


def test_policy_rejects_permission_denied_and_disabled_feature():
    event = make_event()
    decision = make_speak_decision()

    denied = CompanionPolicy().evaluate(
        event, decision, PolicyConditions(permission_denied=True)
    )
    assert denied.allowed is False
    assert denied.effective_action is InteractionAction.SILENCE
    assert denied.reason_code is InteractionReason.PERMISSION_DENIED

    disabled = CompanionPolicy().evaluate(
        event, decision, PolicyConditions(feature_enabled=False)
    )
    assert disabled.allowed is False
    assert disabled.reason_code is InteractionReason.FEATURE_DISABLED

    with pytest.raises(ValueError, match="rejected decision"):
        Action.from_evaluation(denied)


def test_policy_modified_wait_is_allowed_but_not_actionable():
    evaluation = CompanionPolicy().evaluate(
        make_event(),
        make_speak_decision(),
        PolicyConditions(cooldown_active=True, can_wait=True),
    )

    assert evaluation.allowed is True
    assert evaluation.effective_action is InteractionAction.WAIT
    with pytest.raises(ValueError, match="executable action"):
        Action.from_evaluation(evaluation)


def test_policy_requires_matching_event():
    with pytest.raises(ValueError, match="must match"):
        CompanionPolicy().evaluate(
            make_event(), make_speak_decision(event_id="event-2")
        )


# Action --------------------------------------------------------------------


def test_action_requires_allowed_evaluation():
    evaluation = allowed_evaluation()
    action = Action.from_evaluation(evaluation, {"channel": "terminal"})

    assert action.action is InteractionAction.SPEAK
    assert action.decision_id == evaluation.decision.decision_id
    assert dict(action.parameters) == {"channel": "terminal"}

    with pytest.raises(ValueError, match="rejected decision"):
        Action.from_evaluation(denied_evaluation())
    with pytest.raises(ValueError, match="Action must be"):
        Action(
            event_id="event-1",
            decision_id=evaluation.decision.decision_id,
            action=InteractionAction.WAIT,
        )


# Outcome -------------------------------------------------------------------


def test_outcome_proposed_is_not_success():
    proposed = Outcome.proposed(make_speak_decision())

    assert proposed.status is OutcomeStatus.PROPOSED
    assert proposed.action_id is None
    assert proposed.evidence is None
    assert proposed.status is not OutcomeStatus.SUCCEEDED


def test_outcome_status_constraints():
    evaluation = allowed_evaluation()
    action = Action.from_evaluation(evaluation)
    executed = Outcome.executed(action)
    succeeded = Outcome.succeeded(action, "assistant-message-1")
    failed = Outcome.failed(action, "provider error")
    cancelled = Outcome.cancelled(action)
    rejected = Outcome.rejected(denied_evaluation())

    assert executed.status is OutcomeStatus.EXECUTED
    assert succeeded.status is OutcomeStatus.SUCCEEDED
    assert failed.status is OutcomeStatus.FAILED
    assert cancelled.status is OutcomeStatus.CANCELLED
    assert rejected.status is OutcomeStatus.REJECTED
    assert succeeded.evidence == "assistant-message-1"
    assert failed.evidence == "provider error"

    with pytest.raises(ValueError, match="evidence"):
        Outcome.succeeded(action, "")
    with pytest.raises(ValueError, match="require an action_id"):
        Outcome(
            event_id="event-1", decision_id="decision-1", status=OutcomeStatus.SUCCEEDED,
            detail="Succeeded.", evidence="evidence",
        )
    with pytest.raises(ValueError, match="cannot carry an action_id"):
        Outcome(
            event_id="event-1", decision_id="decision-1", status=OutcomeStatus.PROPOSED,
            detail="Proposed.", action_id=action.action_id,
        )
    with pytest.raises(ValueError, match="Cannot record an allowed evaluation as rejected"):
        Outcome.rejected(evaluation)
    with pytest.raises(ValueError, match="Cannot record a rejected evaluation as allowed"):
        Outcome.allowed(denied_evaluation())


# State ---------------------------------------------------------------------


def test_state_starts_without_claims():
    state = CompanionState()

    assert state.revision == 0
    assert state.last_event_id is None
    assert state.last_outcome_id is None
    assert state.last_observed_at is None
    assert state.conversation_active is False
    assert state.pending_presence_notices == 0
    for forbidden in ("awareness", "consciousness", "emotion", "waiting", "offline_activity"):
        assert not hasattr(state, forbidden)


def test_state_update_is_explicit_and_monotonic():
    event = make_event()
    evaluation = allowed_evaluation()
    action = Action.from_evaluation(evaluation)
    outcome = Outcome.succeeded(action, "assistant-message-1")
    state = CompanionState()

    updated = state.updated(
        event, outcome, NOW, conversation_active=False, pending_presence_notices=2
    )

    assert updated.revision == 1
    assert updated.last_event_id == event.event_id
    assert updated.last_outcome_id == outcome.outcome_id
    assert updated.last_observed_at == NOW
    assert updated.conversation_active is False
    assert updated.pending_presence_notices == 2

    with pytest.raises(ValueError, match="cannot move backwards"):
        updated.updated(event, outcome, NOW - timedelta(seconds=1))
    with pytest.raises(ValueError, match="proposed outcome"):
        state.updated(event, Outcome.proposed(evaluation.decision), NOW)
    with pytest.raises(ValueError, match="pending_presence_notices"):
        CompanionState(pending_presence_notices=-1)


def test_state_rejects_inconsistent_initial_values():
    with pytest.raises(ValueError, match="last_outcome_id requires"):
        CompanionState(last_outcome_id="outcome-1")
    with pytest.raises(ValueError, match="last_event_id requires"):
        CompanionState(last_event_id="event-1")
    with pytest.raises(ValueError, match="last_observed_at requires"):
        CompanionState(last_observed_at=NOW)
    with pytest.raises(ValueError, match="timezone-aware"):
        CompanionState(last_event_id="event-1", last_observed_at=datetime.now())


# Contract and soul traceability -------------------------------------------


def test_contract_does_not_define_autonomous_actions():
    assert not hasattr(contract_module, "START_TASK")
    assert "START_TASK" not in {action.name for action in InteractionAction}
    assert "SCHEDULE" not in {action.name for action in InteractionAction}


def test_soul_source_copy_and_contract_are_traceable():
    source = PROJECT_ROOT / "docs/identity/source/soul.txt"
    contract = PROJECT_ROOT / "docs/identity/soul_contract.md"
    provenance = PROJECT_ROOT / "docs/identity/SOUL_PROVENANCE.md"

    assert source.exists()
    assert contract.exists()
    assert provenance.exists()

    data = source.read_bytes()
    assert len(data) == 26711
    assert data.startswith(b"Title: I Built An AI Companion")
    assert b"The Animas are not real" in data

    text = contract.read_text(encoding="utf-8")
    for label in (
        "SOURCE-DERIVED",
        "EXISTING-ARCHITECTURE",
        "NEW-ENGINEERING-INVARIANT",
        "PROMPT-DEFINED / BEHAVIOR-NOT-VERIFIED",
    ):
        assert label in text
    assert "identity.toml" in text
    assert "impersonat" in text.lower()


def test_identity_prompt_contains_honesty_boundary():
    identity = load_identity(PROJECT_ROOT / "identity.toml")
    prompt = identity.to_prompt()

    assert "Do not claim memories, experiences, observations, or capabilities that do not exist." in prompt
    assert "Distinguish observation from inference" in prompt
