"""Typed contract primitives for the Companion Runtime Initiative (CRI).

This module defines validated records only. It starts no worker, schedules no
work, executes no autonomous action, and does not replace Runtime.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from types import MappingProxyType
from uuid import uuid4

from .interaction_policy import (
    InteractionAction,
    InteractionEvent,
    InteractionPolicy,
    InteractionReason,
    PolicyConditions,
    PolicyDecision,
)


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_MAX_TEXT = 2000
_EXECUTABLE_ACTIONS = frozenset({
    InteractionAction.SPEAK,
    InteractionAction.EXPLORE,
    InteractionAction.REQUEST_PERMISSION,
})


def _new_id() -> str:
    return str(uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _require_text(value: object, name: str, maximum: int = _MAX_TEXT) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    clean = value.strip()
    if len(clean) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return clean


def _require_identifier(value: object, name: str) -> str:
    text = _require_text(value, name, 96)
    if not _SAFE_IDENTIFIER.fullmatch(text):
        raise ValueError(f"{name} must be a short opaque identifier")
    return text


def _require_aware(value: object, name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware datetime")
    return value


def _require_confidence(value: object, name: str = "confidence") -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not isfinite(value) or not 0.0 <= float(value) <= 1.0):
        raise ValueError(f"{name} must be a finite number between 0 and 1")
    return float(value)


def _freeze_mapping(value: object, name: str) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping of strings to strings")
    clean: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not _SAFE_IDENTIFIER.fullmatch(key):
            raise ValueError(f"{name} keys must be short opaque identifiers")
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name} values must be non-empty strings")
        text = item.strip()
        if len(text) > 1000:
            raise ValueError(f"{name} values exceed 1000 characters")
        clean[key] = text
    return MappingProxyType(clean)


class CompanionEventType(str, Enum):
    CONVERSATION_TURN_COMPLETED = "conversation_turn_completed"
    USER_MESSAGE_RECEIVED = "user_message_received"
    EXPLORATION_QUESTION_READY = "exploration_question_ready"
    PRESENCE_NOTICE_READY = "presence_notice_ready"
    INVESTIGATION_COMPLETED = "investigation_completed"


class CompanionEventSource(str, Enum):
    CONVERSATION = "conversation"
    EXPLORATION = "exploration"
    PRESENCE = "presence"
    INVESTIGATION = "investigation"


@dataclass(frozen=True)
class CompanionEvent:
    """A validated event entering the companion system."""

    event_id: str
    event_type: CompanionEventType
    occurred_at: datetime
    source: CompanionEventSource
    session_id: str | None = None
    correlation_id: str | None = None
    payload: Mapping[str, str] = field(default_factory=dict)
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_identifier(self.event_id, "event_id"))
        if not isinstance(self.event_type, CompanionEventType):
            raise ValueError("event_type must be a supported CompanionEventType")
        object.__setattr__(self, "occurred_at", _require_aware(self.occurred_at, "occurred_at"))
        if not isinstance(self.source, CompanionEventSource):
            raise ValueError("source must be a supported CompanionEventSource")
        if self.session_id is not None:
            object.__setattr__(self, "session_id", _require_identifier(self.session_id, "session_id"))
        if self.correlation_id is not None:
            object.__setattr__(
                self, "correlation_id", _require_identifier(self.correlation_id, "correlation_id")
            )
        object.__setattr__(self, "payload", _freeze_mapping(self.payload, "payload"))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _require_aware(self.expires_at, "expires_at"))

    def to_dict(self) -> dict[str, object]:
        return {
            "event_id": self.event_id,
            "event_type": self.event_type.value,
            "occurred_at": self.occurred_at.isoformat(),
            "source": self.source.value,
            "session_id": self.session_id,
            "correlation_id": self.correlation_id,
            "payload": dict(self.payload),
            "expires_at": self.expires_at.isoformat() if self.expires_at is not None else None,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False)


class EvidenceKind(str, Enum):
    CONFIRMED = "confirmed"
    CURRENT = "current"
    INFERRED = "inferred"
    MISSING = "missing"


@dataclass(frozen=True)
class ContextItem:
    """One evidence item; its kind is never implicit."""

    kind: EvidenceKind
    content: str
    source_id: str | None = None
    confidence: float | None = None
    observed_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EvidenceKind):
            raise ValueError("kind must be a supported EvidenceKind")
        object.__setattr__(self, "content", _require_text(self.content, "content"))
        if self.source_id is not None:
            object.__setattr__(self, "source_id", _require_identifier(self.source_id, "source_id"))
        if self.confidence is not None:
            object.__setattr__(self, "confidence", _require_confidence(self.confidence))
        if self.observed_at is not None:
            object.__setattr__(self, "observed_at", _require_aware(self.observed_at, "observed_at"))

        if self.kind is EvidenceKind.CONFIRMED and self.source_id is None:
            raise ValueError("confirmed context requires a source_id")
        if self.kind is EvidenceKind.INFERRED and self.confidence is None:
            raise ValueError("inferred context requires a confidence")
        if self.kind is EvidenceKind.MISSING and (
                self.source_id is not None or self.confidence is not None):
            raise ValueError("missing context cannot claim a source or confidence")
        if self.kind is EvidenceKind.CURRENT and self.confidence is not None:
            raise ValueError("current interaction context cannot carry inference confidence")

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "content": self.content,
            "source_id": self.source_id,
            "confidence": self.confidence,
            "observed_at": self.observed_at.isoformat() if self.observed_at is not None else None,
        }


@dataclass(frozen=True)
class CompanionContext:
    """The minimum evidence available to a decision process."""

    event: CompanionEvent
    items: tuple[ContextItem, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.event, CompanionEvent):
            raise ValueError("event must be a CompanionEvent")
        if isinstance(self.items, (str, bytes)) or not isinstance(self.items, Sequence):
            raise ValueError("items must be a sequence of ContextItem values")
        items = tuple(self.items)
        if any(not isinstance(item, ContextItem) for item in items):
            raise ValueError("items must contain only ContextItem values")
        object.__setattr__(self, "items", items)

    def of_kind(self, kind: EvidenceKind) -> tuple[ContextItem, ...]:
        if not isinstance(kind, EvidenceKind):
            raise ValueError("kind must be a supported EvidenceKind")
        return tuple(item for item in self.items if item.kind is kind)

    @property
    def confirmed(self) -> tuple[ContextItem, ...]:
        return self.of_kind(EvidenceKind.CONFIRMED)

    @property
    def current(self) -> tuple[ContextItem, ...]:
        return self.of_kind(EvidenceKind.CURRENT)

    @property
    def inferred(self) -> tuple[ContextItem, ...]:
        return self.of_kind(EvidenceKind.INFERRED)

    @property
    def missing(self) -> tuple[ContextItem, ...]:
        return self.of_kind(EvidenceKind.MISSING)

    @property
    def has_missing(self) -> bool:
        return bool(self.missing)

    def to_dict(self) -> dict[str, object]:
        return {"event": self.event.to_dict(), "items": [item.to_dict() for item in self.items]}


@dataclass(frozen=True)
class Decision:
    """A proposed response to an event."""

    event_id: str
    action: InteractionAction
    rationale: str
    decision_id: str = field(default_factory=_new_id)
    message: str | None = None
    permission_scope: str | None = None
    not_before: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_identifier(self.event_id, "event_id"))
        object.__setattr__(
            self, "decision_id", _require_identifier(self.decision_id, "decision_id")
        )
        if not isinstance(self.action, InteractionAction):
            raise ValueError("action must be a supported InteractionAction")
        object.__setattr__(self, "rationale", _require_text(self.rationale, "rationale", 1000))

        if self.action is InteractionAction.SPEAK:
            if self.message is None:
                raise ValueError("SPEAK decisions require a message")
            object.__setattr__(self, "message", _require_text(self.message, "message"))
        elif self.message is not None:
            raise ValueError("Only SPEAK decisions may contain a message")

        if self.action is InteractionAction.REQUEST_PERMISSION:
            if self.permission_scope is None:
                raise ValueError("REQUEST_PERMISSION decisions require a permission_scope")
            object.__setattr__(
                self,
                "permission_scope",
                _require_identifier(self.permission_scope, "permission_scope"),
            )
        elif self.permission_scope is not None:
            raise ValueError("Only REQUEST_PERMISSION decisions may name a permission")

        if self.not_before is not None:
            if self.action is not InteractionAction.WAIT:
                raise ValueError("Only WAIT decisions may carry not_before")
            object.__setattr__(self, "not_before", _require_aware(self.not_before, "not_before"))

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "event_id": self.event_id,
            "action": self.action.value,
            "rationale": self.rationale,
            "message": self.message,
            "permission_scope": self.permission_scope,
            "not_before": self.not_before.isoformat() if self.not_before is not None else None,
        }


@dataclass(frozen=True)
class AutonomyDecision:
    """One deterministic proposal to perform a bounded autonomous action.

    The decision is a proposal only: it carries the trigger event, the proposed
    action category and action, the bounded scope, the required permission
    identifiers, the evidence source identifiers, a reason, and an optional
    execution deadline. No LLM participates in its creation.
    """

    decision_id: str
    trigger_event_id: str
    proposed_action: InteractionAction
    action_category: str
    scope: Mapping[str, str]
    required_permissions: tuple[str, ...]
    evidence: tuple[str, ...]
    reason: str
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _require_identifier(self.decision_id, "decision_id"))
        object.__setattr__(
            self, "trigger_event_id", _require_identifier(self.trigger_event_id, "trigger_event_id")
        )
        if not isinstance(self.proposed_action, InteractionAction):
            raise ValueError("proposed_action must be a supported InteractionAction")
        object.__setattr__(self, "action_category", _require_identifier(self.action_category, "action_category"))
        if isinstance(self.scope, (str, bytes)) or not isinstance(self.scope, Mapping):
            raise ValueError("scope must be a mapping of strings to strings")
        clean_scope: dict[str, str] = {}
        for key, value in dict(self.scope).items():
            clean_scope[_require_identifier(key, "scope key")] = _require_text(value, "scope value", 1000)
        object.__setattr__(self, "scope", MappingProxyType(clean_scope))
        permissions = self.required_permissions
        if isinstance(permissions, (str, bytes)) or not isinstance(permissions, (tuple, list)) or not permissions:
            raise ValueError("required_permissions must be a non-empty sequence of permission identifiers")
        object.__setattr__(
            self, "required_permissions",
            tuple(_require_identifier(item, "required permission") for item in permissions),
        )
        evidence = self.evidence
        if isinstance(evidence, (str, bytes)) or not isinstance(evidence, (tuple, list)):
            raise ValueError("evidence must be a sequence of source identifiers")
        object.__setattr__(
            self, "evidence",
            tuple(_require_identifier(item, "evidence source") for item in evidence),
        )
        object.__setattr__(self, "reason", _require_text(self.reason, "reason", 1000))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _require_aware(self.expires_at, "expires_at"))

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "trigger_event_id": self.trigger_event_id,
            "proposed_action": self.proposed_action.value,
            "action_category": self.action_category,
            "scope": dict(self.scope),
            "required_permissions": list(self.required_permissions),
            "evidence": list(self.evidence),
            "reason": self.reason,
            "expires_at": self.expires_at.isoformat() if self.expires_at is not None else None,
        }


class InitiativeAction(str, Enum):
    """The bounded initiative decision vocabulary for one trigger event."""

    COMMUNICATE = "COMMUNICATE"
    WAIT = "WAIT"
    SILENCE = "SILENCE"
    REQUEST_PERMISSION = "REQUEST_PERMISSION"
    REJECT = "REJECT"


@dataclass(frozen=True)
class InitiativeDecision:
    """One deterministic proposal to evaluate an initiative from a real event.

    The decision is a proposal only. It carries the trigger event, the proposed
    initiative action, the bounded action category, the available evidence with
    its classifications, a proposed response for COMMUNICATION, the permission
    identifiers the initiative would need, a reason, and an optional validity
    boundary. No LLM participates in its creation. A REJECT proposal carries the
    refusal reason; deterministic policy conditions resolve it to a refusal.
    """

    decision_id: str
    trigger_event_id: str
    source: CompanionEventSource
    proposed_action: InitiativeAction
    action_category: str
    evidence: tuple[str, ...]
    evidence_kinds: tuple[EvidenceKind, ...]
    response: str | None = None
    required_permissions: tuple[str, ...] = ()
    reason: str = "The bounded investigation completed with sufficient evidence."
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "decision_id", _require_identifier(self.decision_id, "decision_id"))
        object.__setattr__(
            self, "trigger_event_id", _require_identifier(self.trigger_event_id, "trigger_event_id")
        )
        if not isinstance(self.source, CompanionEventSource):
            raise ValueError("source must be a supported CompanionEventSource")
        if not isinstance(self.proposed_action, InitiativeAction):
            raise ValueError("proposed_action must be a supported InitiativeAction")
        object.__setattr__(self, "action_category", _require_identifier(self.action_category, "action_category"))
        evidence = self.evidence
        if isinstance(evidence, (str, bytes)) or not isinstance(evidence, (tuple, list)):
            raise ValueError("evidence must be a sequence of source identifiers")
        object.__setattr__(
            self, "evidence",
            tuple(_require_identifier(item, "evidence source") for item in evidence),
        )
        kinds = self.evidence_kinds
        if (
            isinstance(kinds, (str, bytes))
            or not isinstance(kinds, (tuple, list))
            or any(not isinstance(kind, EvidenceKind) for kind in kinds)
        ):
            raise ValueError("evidence_kinds must be a sequence of EvidenceKind values")
        object.__setattr__(self, "evidence_kinds", tuple(kinds))
        if self.response is not None:
            object.__setattr__(self, "response", _require_text(self.response, "response", 1000))
        permissions = self.required_permissions
        if isinstance(permissions, (str, bytes)) or not isinstance(permissions, (tuple, list)):
            raise ValueError("required_permissions must be a sequence of permission identifiers")
        clean_permissions = tuple(_require_identifier(item, "required permission") for item in permissions)
        object.__setattr__(self, "required_permissions", clean_permissions)
        if self.proposed_action in (InitiativeAction.COMMUNICATE, InitiativeAction.REQUEST_PERMISSION) and not clean_permissions:
            raise ValueError("COMMUNICATE and REQUEST_PERMISSION initiatives require a permission scope")
        object.__setattr__(self, "reason", _require_text(self.reason, "reason", 1000))
        if self.expires_at is not None:
            object.__setattr__(self, "expires_at", _require_aware(self.expires_at, "expires_at"))

    def to_dict(self) -> dict[str, object]:
        return {
            "decision_id": self.decision_id,
            "trigger_event_id": self.trigger_event_id,
            "source": self.source.value,
            "proposed_action": self.proposed_action.value,
            "action_category": self.action_category,
            "evidence": list(self.evidence),
            "evidence_kinds": [kind.value for kind in self.evidence_kinds],
            "response": self.response,
            "required_permissions": list(self.required_permissions),
            "reason": self.reason,
            "expires_at": self.expires_at.isoformat() if self.expires_at is not None else None,
        }


@dataclass(frozen=True)
class PolicyEvaluation:
    """The deterministic result of applying policy to a decision."""

    decision: Decision
    allowed: bool
    effective_action: InteractionAction
    reason_code: InteractionReason
    policy_decision: PolicyDecision

    def __post_init__(self) -> None:
        if not isinstance(self.decision, Decision):
            raise ValueError("decision must be a Decision")
        if not isinstance(self.allowed, bool):
            raise ValueError("allowed must be a boolean")
        if not isinstance(self.effective_action, InteractionAction):
            raise ValueError("effective_action must be a supported InteractionAction")
        if not isinstance(self.reason_code, InteractionReason):
            raise ValueError("reason_code must be a supported InteractionReason")
        if not isinstance(self.policy_decision, PolicyDecision):
            raise ValueError("policy_decision must be a PolicyDecision")
        if not self.allowed and self.effective_action is not InteractionAction.SILENCE:
            raise ValueError("A denied policy evaluation must resolve to SILENCE")
        if (self.allowed and self.effective_action is InteractionAction.SILENCE
                and self.decision.action is not InteractionAction.SILENCE):
            raise ValueError("A silenced decision cannot be marked allowed")

    @property
    def rejected(self) -> bool:
        return not self.allowed

    def to_dict(self) -> dict[str, object]:
        return {
            "decision": self.decision.to_dict(),
            "allowed": self.allowed,
            "effective_action": self.effective_action.value,
            "reason_code": self.reason_code.value,
            "policy_decision": {
                "action": self.policy_decision.action.value,
                "reason_code": self.policy_decision.reason_code.value,
                "source_event_id": self.policy_decision.source_event_id,
                "dedup_key": self.policy_decision.dedup_key,
                "message": self.policy_decision.message,
                "required_permission": self.policy_decision.required_permission,
                "not_before": (
                    self.policy_decision.not_before.isoformat()
                    if self.policy_decision.not_before is not None else None
                ),
                "expires_at": (
                    self.policy_decision.expires_at.isoformat()
                    if self.policy_decision.expires_at is not None else None
                ),
            },
        }


class CompanionPolicy:
    """Deterministic policy adapter; permission logic stays in InteractionPolicy."""

    def __init__(self, policy: InteractionPolicy | None = None):
        self._policy = policy or InteractionPolicy()

    def evaluate(
        self,
        event: CompanionEvent,
        decision: Decision,
        conditions: PolicyConditions | None = None,
    ) -> PolicyEvaluation:
        if not isinstance(event, CompanionEvent):
            raise ValueError("event must be a CompanionEvent")
        if not isinstance(decision, Decision):
            raise ValueError("decision must be a Decision")
        if event.event_id != decision.event_id:
            raise ValueError("Decision event_id must match the event")
        if conditions is None:
            conditions = PolicyConditions()
        if not isinstance(conditions, PolicyConditions):
            raise ValueError("conditions must be PolicyConditions")

        conditions = replace(conditions, requested_action=decision.action)
        interaction_event = InteractionEvent(
            event_id=event.event_id,
            source=event.source.value,
            occurred_at=event.occurred_at,
            dedup_key=event.event_id,
            permission_scope=decision.permission_scope or "none",
            payload=(
                decision.message
                if decision.action is InteractionAction.SPEAK
                else decision.rationale
            ),
            expires_at=event.expires_at,
        )
        result = self._policy.decide(interaction_event, conditions)
        effective = result.action
        allowed = not (
            effective is InteractionAction.SILENCE
            and decision.action is not InteractionAction.SILENCE
        )
        return PolicyEvaluation(decision, allowed, effective, result.reason_code, result)


@dataclass(frozen=True)
class Action:
    """A permitted, contract-level operation. It does not execute anything."""

    event_id: str
    decision_id: str
    action: InteractionAction
    action_id: str = field(default_factory=_new_id)
    parameters: Mapping[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_identifier(self.event_id, "event_id"))
        object.__setattr__(
            self, "decision_id", _require_identifier(self.decision_id, "decision_id")
        )
        object.__setattr__(self, "action_id", _require_identifier(self.action_id, "action_id"))
        if not isinstance(self.action, InteractionAction):
            raise ValueError("action must be a supported InteractionAction")
        if self.action not in _EXECUTABLE_ACTIONS:
            raise ValueError("Action must be SPEAK, EXPLORE, or REQUEST_PERMISSION")
        object.__setattr__(self, "parameters", _freeze_mapping(self.parameters, "parameters"))
        object.__setattr__(self, "created_at", _require_aware(self.created_at, "created_at"))

    @classmethod
    def from_evaluation(
        cls,
        evaluation: PolicyEvaluation,
        parameters: Mapping[str, str] | None = None,
    ) -> "Action":
        if not isinstance(evaluation, PolicyEvaluation):
            raise ValueError("evaluation must be a PolicyEvaluation")
        if not evaluation.allowed:
            raise ValueError("Cannot create an action from a rejected decision")
        if evaluation.effective_action not in _EXECUTABLE_ACTIONS:
            raise ValueError("Policy did not permit an executable action")
        return cls(
            event_id=evaluation.decision.event_id,
            decision_id=evaluation.decision.decision_id,
            action=evaluation.effective_action,
            parameters=parameters or {},
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "event_id": self.event_id,
            "decision_id": self.decision_id,
            "action": self.action.value,
            "parameters": dict(self.parameters),
            "created_at": self.created_at.isoformat(),
        }


class OutcomeStatus(str, Enum):
    PROPOSED = "proposed"
    ALLOWED = "allowed"
    EXECUTED = "executed"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


_NO_ACTION_STATUSES = frozenset({
    OutcomeStatus.PROPOSED,
    OutcomeStatus.ALLOWED,
    OutcomeStatus.REJECTED,
})
_ACTION_REQUIRED_STATUSES = frozenset({
    OutcomeStatus.EXECUTED,
    OutcomeStatus.SUCCEEDED,
    OutcomeStatus.FAILED,
    OutcomeStatus.CANCELLED,
})
_EVIDENCE_REQUIRED_STATUSES = frozenset({OutcomeStatus.SUCCEEDED, OutcomeStatus.FAILED})


@dataclass(frozen=True)
class Outcome:
    """A truthful record of what actually happened."""

    event_id: str
    decision_id: str
    status: OutcomeStatus
    detail: str
    outcome_id: str = field(default_factory=_new_id)
    action_id: str | None = None
    evidence: str | None = None
    created_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _require_identifier(self.event_id, "event_id"))
        object.__setattr__(
            self, "decision_id", _require_identifier(self.decision_id, "decision_id")
        )
        object.__setattr__(self, "outcome_id", _require_identifier(self.outcome_id, "outcome_id"))
        if not isinstance(self.status, OutcomeStatus):
            raise ValueError("status must be a supported OutcomeStatus")
        object.__setattr__(self, "detail", _require_text(self.detail, "detail"))
        if self.action_id is not None:
            object.__setattr__(self, "action_id", _require_identifier(self.action_id, "action_id"))
        if self.evidence is not None:
            object.__setattr__(self, "evidence", _require_text(self.evidence, "evidence"))
        object.__setattr__(self, "created_at", _require_aware(self.created_at, "created_at"))

        if self.status in _NO_ACTION_STATUSES and self.action_id is not None:
            raise ValueError(f"{self.status.value} outcomes cannot carry an action_id")
        if self.status in _ACTION_REQUIRED_STATUSES and self.action_id is None:
            raise ValueError(f"{self.status.value} outcomes require an action_id")
        if self.status in _EVIDENCE_REQUIRED_STATUSES and self.evidence is None:
            raise ValueError(f"{self.status.value} outcomes require execution evidence")
        if self.status is OutcomeStatus.PROPOSED and self.evidence is not None:
            raise ValueError("PROPOSED outcomes cannot claim execution evidence")

    @classmethod
    def proposed(cls, decision: Decision, detail: str = "Decision proposed") -> "Outcome":
        return cls(
            event_id=decision.event_id,
            decision_id=decision.decision_id,
            status=OutcomeStatus.PROPOSED,
            detail=detail,
        )

    @classmethod
    def allowed(cls, evaluation: PolicyEvaluation, detail: str | None = None) -> "Outcome":
        if not evaluation.allowed:
            raise ValueError("Cannot record a rejected evaluation as allowed")
        return cls(
            event_id=evaluation.decision.event_id,
            decision_id=evaluation.decision.decision_id,
            status=OutcomeStatus.ALLOWED,
            detail=detail or f"Policy allowed: {evaluation.reason_code.value}",
        )

    @classmethod
    def rejected(cls, evaluation: PolicyEvaluation, detail: str | None = None) -> "Outcome":
        if evaluation.allowed:
            raise ValueError("Cannot record an allowed evaluation as rejected")
        return cls(
            event_id=evaluation.decision.event_id,
            decision_id=evaluation.decision.decision_id,
            status=OutcomeStatus.REJECTED,
            detail=detail or f"Policy rejected: {evaluation.reason_code.value}",
        )

    @classmethod
    def executed(cls, action: Action, detail: str = "Action attempted") -> "Outcome":
        return cls(
            event_id=action.event_id,
            decision_id=action.decision_id,
            status=OutcomeStatus.EXECUTED,
            detail=detail,
            action_id=action.action_id,
        )

    @classmethod
    def succeeded(cls, action: Action, evidence: str, detail: str = "Action succeeded") -> "Outcome":
        return cls(
            event_id=action.event_id,
            decision_id=action.decision_id,
            status=OutcomeStatus.SUCCEEDED,
            detail=detail,
            action_id=action.action_id,
            evidence=evidence,
        )

    @classmethod
    def failed(cls, action: Action, evidence: str, detail: str = "Action failed") -> "Outcome":
        return cls(
            event_id=action.event_id,
            decision_id=action.decision_id,
            status=OutcomeStatus.FAILED,
            detail=detail,
            action_id=action.action_id,
            evidence=evidence,
        )

    @classmethod
    def cancelled(cls, action: Action, detail: str = "Action cancelled") -> "Outcome":
        return cls(
            event_id=action.event_id,
            decision_id=action.decision_id,
            status=OutcomeStatus.CANCELLED,
            detail=detail,
            action_id=action.action_id,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "outcome_id": self.outcome_id,
            "event_id": self.event_id,
            "decision_id": self.decision_id,
            "action_id": self.action_id,
            "status": self.status.value,
            "detail": self.detail,
            "evidence": self.evidence,
            "created_at": self.created_at.isoformat(),
        }


@dataclass(frozen=True)
class CompanionState:
    """Minimal evidence-based state; it claims no awareness or experience."""

    revision: int = 0
    last_event_id: str | None = None
    last_outcome_id: str | None = None
    last_observed_at: datetime | None = None
    conversation_active: bool = False
    pending_presence_notices: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.revision, bool) or not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        if isinstance(self.pending_presence_notices, bool) or not isinstance(
                self.pending_presence_notices, int) or self.pending_presence_notices < 0:
            raise ValueError("pending_presence_notices must be a non-negative integer")
        if not isinstance(self.conversation_active, bool):
            raise ValueError("conversation_active must be a boolean")
        if self.last_event_id is not None:
            object.__setattr__(
                self, "last_event_id", _require_identifier(self.last_event_id, "last_event_id")
            )
        if self.last_outcome_id is not None:
            object.__setattr__(
                self, "last_outcome_id", _require_identifier(self.last_outcome_id, "last_outcome_id")
            )
        if self.last_observed_at is not None:
            object.__setattr__(
                self, "last_observed_at", _require_aware(self.last_observed_at, "last_observed_at")
            )

        if self.last_event_id is None and self.last_outcome_id is not None:
            raise ValueError("last_outcome_id requires a last_event_id")
        if self.last_event_id is not None and self.last_observed_at is None:
            raise ValueError("last_event_id requires last_observed_at")
        if self.last_event_id is None and self.last_observed_at is not None:
            raise ValueError("last_observed_at requires a last_event_id")

    def updated(
        self,
        event: CompanionEvent,
        outcome: Outcome,
        observed_at: datetime,
        *,
        conversation_active: bool | None = None,
        pending_presence_notices: int | None = None,
    ) -> "CompanionState":
        if not isinstance(event, CompanionEvent):
            raise ValueError("event must be a CompanionEvent")
        if not isinstance(outcome, Outcome):
            raise ValueError("outcome must be an Outcome")
        if outcome.event_id != event.event_id:
            raise ValueError("Outcome event_id must match the event")
        observed_at = _require_aware(observed_at, "observed_at")
        if outcome.status is OutcomeStatus.PROPOSED:
            raise ValueError("State cannot be updated from a proposed outcome")
        if self.last_observed_at is not None and observed_at < self.last_observed_at:
            raise ValueError("observed_at cannot move backwards")
        if conversation_active is None:
            conversation_active = self.conversation_active
        if pending_presence_notices is None:
            pending_presence_notices = self.pending_presence_notices
        return CompanionState(
            revision=self.revision + 1,
            last_event_id=event.event_id,
            last_outcome_id=outcome.outcome_id,
            last_observed_at=observed_at,
            conversation_active=conversation_active,
            pending_presence_notices=pending_presence_notices,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "revision": self.revision,
            "last_event_id": self.last_event_id,
            "last_outcome_id": self.last_outcome_id,
            "last_observed_at": (
                self.last_observed_at.isoformat()
                if self.last_observed_at is not None else None
            ),
            "conversation_active": self.conversation_active,
            "pending_presence_notices": self.pending_presence_notices,
        }


__all__ = [
    "Action",
    "AutonomyDecision",
    "CompanionContext",
    "CompanionEvent",
    "CompanionEventSource",
    "CompanionEventType",
    "CompanionPolicy",
    "CompanionState",
    "ContextItem",
    "Decision",
    "EvidenceKind",
    "InitiativeAction",
    "InitiativeDecision",
    "Outcome",
    "OutcomeStatus",
    "PolicyEvaluation",
]
