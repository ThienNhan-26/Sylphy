"""Pure, immutable decision contracts for the Entity Brain.

No runtime, persistence, UI, model, filesystem, or execution dependencies are
permitted in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from hashlib import sha256


class Behaviour(str, Enum):
    RESTING = "resting"
    OBSERVING = "observing"
    EVALUATING = "evaluating"
    PURSUING = "pursuing"
    WAITING = "waiting"
    REFLECTING = "reflecting"
    YIELDING = "yielding"
    RECOVERING = "recovering"


class GoalStatus(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    WAITING = "waiting"
    SUSPENDED = "suspended"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    ABANDONED = "abandoned"


class GoalOutcome(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    WAITING = "waiting"
    ABANDONED = "abandoned"


def _bounded(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be a finite value in [0, 1]")
    return value


@dataclass(frozen=True, slots=True)
class CognitionRequestProposal:
    """A pure Brain proposal for bounded interpretation of current evidence."""

    request_id: str
    operation: str
    purpose: str
    source_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.request_id or not self.operation or not self.purpose.strip():
            raise ValueError("cognition proposal identity, operation and purpose are required")
        if not self.source_refs or any(not item.strip() for item in self.source_refs):
            raise ValueError("cognition proposal requires non-blank source references")
        if len(set(self.source_refs)) != len(self.source_refs):
            raise ValueError("cognition proposal source references must be unique")


@dataclass(frozen=True, slots=True)
class CognitionFeedback:
    """Provider result handed back to the pure Brain for provenance review."""

    request_id: str
    status: str
    source_refs: tuple[str, ...]
    summary: str = ""
    summary_evidence_refs: tuple[str, ...] = ()
    findings: tuple[tuple[str, str, float, tuple[str, ...]], ...] = ()
    proposals: tuple[tuple[str, str, str, tuple[str, ...]], ...] = ()
    elapsed_ms: int = 0
    model: str | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class CognitionReceipt:
    """Durable, non-executable record of Brain's review of one model result."""

    request_id: str
    status: str
    accepted_as_evidence: bool
    review_reason: str
    source_refs: tuple[str, ...]
    summary: str = ""
    findings: tuple[str, ...] = ()
    proposals: tuple[str, ...] = ()
    model: str | None = None
    elapsed_ms: int = 0
    error_code: str | None = None
    processed_at_virtual: float = 0.0


@dataclass(frozen=True, slots=True)
class DriveState:
    """Operational pressure values; these are not claims of human emotion."""

    curiosity: float = 0.25
    continuity: float = 0.30
    social_responsiveness: float = 0.20
    self_maintenance: float = 0.10
    resource_conservation: float = 0.20

    def __post_init__(self) -> None:
        for name in (
            "curiosity",
            "continuity",
            "social_responsiveness",
            "self_maintenance",
            "resource_conservation",
        ):
            object.__setattr__(self, name, _bounded(name, getattr(self, name)))


@dataclass(frozen=True, slots=True)
class DriveInputs:
    """Explicit, normalized pressures supplied by the Runtime/World boundary."""

    curiosity: float = 0.0
    continuity: float = 0.0
    social_responsiveness: float = 0.0
    self_maintenance: float = 0.0
    resource_pressure: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "curiosity",
            "continuity",
            "social_responsiveness",
            "self_maintenance",
            "resource_pressure",
        ):
            object.__setattr__(self, name, _bounded(name, getattr(self, name)))


@dataclass(frozen=True, slots=True)
class NoveltyRecord:
    subject: str
    last_seen_virtual: float
    visit_count: int = 1
    depth: int = 1

    def __post_init__(self) -> None:
        if not self.subject.strip():
            raise ValueError("novelty subject must not be blank")
        if not math.isfinite(self.last_seen_virtual) or self.last_seen_virtual < 0:
            raise ValueError("last_seen_virtual must be finite and non-negative")
        if self.visit_count < 1 or self.depth < 0:
            raise ValueError("visit_count must be positive and depth non-negative")


@dataclass(frozen=True, slots=True)
class Goal:
    goal_id: str
    kind: str
    subject: str
    source: str
    priority: float
    urgency: float
    desired_outcome: str
    target: str = ""
    status: GoalStatus = GoalStatus.ACTIVE
    created_at_virtual: float = 0.0
    updated_at_virtual: float = 0.0
    cost: float = 0.2
    risk: float = 0.1
    relevance: float = 0.6
    required_permissions: tuple[str, ...] = ()
    permission_scope: str = "unknown"
    deadline_at_virtual: float | None = None
    cooldown_until_virtual: float = 0.0
    failure_count: int = 0
    progress: float = 0.0
    completion_evidence: tuple[str, ...] = ()
    suspended_at_virtual: float | None = None
    resume_status: GoalStatus | None = None
    attempt_count: int = 0

    def __post_init__(self) -> None:
        if not self.goal_id or not self.kind or not self.subject:
            raise ValueError("goal identity, kind and subject are required")
        if not self.source or not self.permission_scope:
            raise ValueError("goal source and permission scope are required")
        for name in ("priority", "urgency", "cost", "risk", "relevance", "progress"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be a finite value in [0, 1]")
        if self.failure_count < 0 or self.attempt_count < 0:
            raise ValueError("failure_count and attempt_count must be non-negative")
        for name in ("created_at_virtual", "updated_at_virtual", "cooldown_until_virtual"):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and non-negative")
        for name in ("deadline_at_virtual", "suspended_at_virtual"):
            value = getattr(self, name)
            if value is not None and (not math.isfinite(float(value)) or value < 0):
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True, slots=True)
class GoalProposal:
    kind: str
    subject: str
    source: str
    target: str
    priority: float
    urgency: float
    desired_outcome: str
    reason: str
    cost: float = 0.2
    risk: float = 0.1
    relevance: float = 0.6
    required_permissions: tuple[str, ...] = ("OBSERVE_SANDBOX",)
    permission_scope: str = "unknown"
    deadline_seconds: float | None = None

    def __post_init__(self) -> None:
        if not self.kind or not self.subject or not self.source or not self.target:
            raise ValueError("goal proposal requires kind, subject, source and target")
        for name in ("priority", "urgency", "cost", "risk", "relevance"):
            _bounded(name, getattr(self, name))
        if self.deadline_seconds is not None and (
            not math.isfinite(self.deadline_seconds) or self.deadline_seconds < 0
        ):
            raise ValueError("deadline_seconds must be finite and non-negative")
        if not self.permission_scope or not self.required_permissions:
            raise ValueError("goal proposal requires permission scope and permissions")


@dataclass(frozen=True, slots=True)
class GoalFeedback:
    goal_id: str
    outcome: GoalOutcome
    evidence_id: str | None = None
    reason: str | None = None
    progress: float | None = None

    def __post_init__(self) -> None:
        if not self.goal_id.strip():
            raise ValueError("goal_id must not be blank")
        if self.outcome is GoalOutcome.SUCCEEDED and not (self.evidence_id or "").strip():
            raise ValueError("successful goal feedback requires evidence_id")
        if self.progress is not None:
            _bounded("progress", self.progress)


@dataclass(frozen=True, slots=True)
class IntentProposal:
    """A proposal only. Stage 4 Runtime will validate and authorize it."""

    intent_id: str
    goal_id: str
    capability: str
    target: str
    arguments: tuple[tuple[str, str | int | float | bool], ...]
    reason: str
    permission_class: str
    permission_scope: str
    required_resources: tuple[str, ...]
    expected_outcome: str
    priority: float
    source_observation_id: str | None = None

    def __post_init__(self) -> None:
        if not all((self.intent_id, self.goal_id, self.capability, self.target, self.permission_class, self.permission_scope)):
            raise ValueError("intent proposal is missing required identity or permission fields")
        _bounded("priority", self.priority)


@dataclass(frozen=True, slots=True)
class BrainState:
    behaviour: Behaviour = Behaviour.RESTING
    virtual_time: float = 0.0
    drives: DriveState = field(default_factory=DriveState)
    goals: tuple[Goal, ...] = ()
    novelty: tuple[NoveltyRecord, ...] = ()
    yielded: bool = False
    yielded_since_virtual: float | None = None
    transient_latches: tuple[str, ...] = ()
    last_world_version: int = -1
    last_reason: str = "initial"
    tick_count: int = 0
    cognition_requested_refs: tuple[str, ...] = ()
    cognition_receipts: tuple[CognitionReceipt, ...] = ()

    def __post_init__(self) -> None:
        if not math.isfinite(self.virtual_time) or self.virtual_time < 0:
            raise ValueError("virtual_time must be finite and non-negative")
        if self.tick_count < 0:
            raise ValueError("tick_count must be non-negative")
        if self.last_world_version < -1:
            raise ValueError("last_world_version must be >= -1")


@dataclass(frozen=True, slots=True)
class DecisionFrame:
    next_state: BrainState
    behaviour: Behaviour
    transition: tuple[Behaviour, Behaviour] | None
    goal_proposals: tuple[GoalProposal, ...]
    goal_updates: tuple[Goal, ...]
    intent_proposals: tuple[IntentProposal, ...]
    active_goal_ids: tuple[str, ...]
    rationale: tuple[str, ...]
    should_yield: bool
    cognition_proposals: tuple[CognitionRequestProposal, ...] = ()
