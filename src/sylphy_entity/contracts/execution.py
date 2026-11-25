"""Typed contracts for authorized Entity actions (Stage 4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import math
from types import MappingProxyType
from typing import Mapping


class PermissionClass(str, Enum):
    OBSERVE_SANDBOX = "OBSERVE_SANDBOX"
    READ_SANDBOX = "READ_SANDBOX"


class ResourceMode(str, Enum):
    SHARED = "shared"
    EXCLUSIVE = "exclusive"


class ExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"
    REJECTED = "rejected"
    BLOCKED = "blocked"


class IntentOrigin(str, Enum):
    AUTONOMOUS = "autonomous"
    USER = "user"


class CancellationPolicy(str, Enum):
    CANCEL_ON_TAKEOVER = "cancel_on_takeover"
    FINISH_ATOMIC = "finish_atomic"


@dataclass(frozen=True, slots=True)
class ResourceRequirement:
    resource_key: str
    mode: ResourceMode = ResourceMode.SHARED

    def __post_init__(self) -> None:
        if not isinstance(self.resource_key, str) or not self.resource_key.strip():
            raise ValueError("resource_key must be a non-blank string")
        if not isinstance(self.mode, ResourceMode):
            object.__setattr__(self, "mode", ResourceMode(self.mode))


@dataclass(frozen=True, slots=True)
class Intent:
    intent_id: str
    goal_id: str
    capability: str
    target: str
    arguments: tuple[tuple[str, str | int | float | bool], ...]
    reason: str
    preconditions: tuple[str, ...]
    permission_class: PermissionClass
    permission_scope: str
    resources: tuple[ResourceRequirement, ...]
    priority: float
    expected_outcome: str
    idempotency_key: str
    created_at_monotonic: float
    deadline_at_monotonic: float | None
    timeout_seconds: float
    cancellation_policy: CancellationPolicy = CancellationPolicy.CANCEL_ON_TAKEOVER
    origin: IntentOrigin = IntentOrigin.AUTONOMOUS
    source_observation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.permission_class, PermissionClass):
            object.__setattr__(self, "permission_class", PermissionClass(self.permission_class))
        if not isinstance(self.origin, IntentOrigin):
            object.__setattr__(self, "origin", IntentOrigin(self.origin))
        if not isinstance(self.cancellation_policy, CancellationPolicy):
            object.__setattr__(self, "cancellation_policy", CancellationPolicy(self.cancellation_policy))
        if self.source_observation_id is not None and (
            not isinstance(self.source_observation_id, str) or not self.source_observation_id.strip()
        ):
            raise ValueError("source_observation_id must be a non-blank string when provided")
        for name in (
            "intent_id", "goal_id", "capability", "target", "permission_scope",
            "reason", "expected_outcome", "idempotency_key",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-blank string")
        if isinstance(self.priority, bool) or not isinstance(self.priority, (int, float)):
            raise ValueError("priority must be numeric")
        if not math.isfinite(self.priority) or not 0.0 <= self.priority <= 1.0:
            raise ValueError("priority must be finite and in [0, 1]")
        if isinstance(self.created_at_monotonic, bool) or not isinstance(self.created_at_monotonic, (int, float)):
            raise ValueError("created_at_monotonic must be numeric")
        if not math.isfinite(self.created_at_monotonic) or self.created_at_monotonic < 0:
            raise ValueError("created_at_monotonic must be finite and non-negative")
        if self.deadline_at_monotonic is not None and (
            isinstance(self.deadline_at_monotonic, bool)
            or not isinstance(self.deadline_at_monotonic, (int, float))
            or not math.isfinite(self.deadline_at_monotonic)
            or self.deadline_at_monotonic < self.created_at_monotonic
        ):
            raise ValueError("deadline_at_monotonic must be numeric and >= created_at_monotonic")
        if isinstance(self.timeout_seconds, bool) or not isinstance(self.timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be numeric")
        if not math.isfinite(self.timeout_seconds) or not 0.0 < self.timeout_seconds <= 300.0:
            raise ValueError("timeout_seconds must be in (0, 300]")
        resources = tuple(self.resources)
        if not resources or any(not isinstance(item, ResourceRequirement) for item in resources):
            raise ValueError("at least one valid resource requirement is required")
        object.__setattr__(self, "resources", resources)
        preconditions = tuple(self.preconditions)
        if any(not isinstance(item, str) or not item.strip() for item in preconditions):
            raise ValueError("preconditions must contain non-blank strings")
        object.__setattr__(self, "preconditions", preconditions)
        arguments = tuple(self.arguments)
        if any(not isinstance(item, (tuple, list)) or len(item) != 2 for item in arguments):
            raise ValueError("Intent arguments must be name/value pairs")
        if any(not isinstance(key, str) or not key.strip() for key, _ in arguments):
            raise ValueError("Intent argument names must be non-blank strings")
        if len({key for key, _ in arguments}) != len(arguments):
            raise ValueError("Intent argument names must be unique")
        for key, value in arguments:
            if not isinstance(value, (str, int, float, bool)):
                raise ValueError(f"Intent argument {key!r} must be a JSON scalar")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError(f"Intent argument {key!r} must be finite")
        object.__setattr__(self, "arguments", tuple((key, value) for key, value in arguments))

    @property
    def argument_map(self) -> dict[str, str | int | float | bool]:
        return dict(self.arguments)


@dataclass(frozen=True, slots=True)
class ResourceLease:
    lease_id: str
    owner_id: str
    resource_key: str
    mode: ResourceMode
    acquired_at_monotonic: float
    expires_at_monotonic: float

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (self.lease_id, self.owner_id, self.resource_key)):
            raise ValueError("lease identity fields must be non-blank strings")
        if not isinstance(self.mode, ResourceMode):
            object.__setattr__(self, "mode", ResourceMode(self.mode))
        if not math.isfinite(self.acquired_at_monotonic) or not math.isfinite(self.expires_at_monotonic):
            raise ValueError("lease timestamps must be finite")
        if self.expires_at_monotonic <= self.acquired_at_monotonic:
            raise ValueError("lease expiry must be after acquisition")


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    intent_id: str
    status: ExecutionStatus
    started_at: datetime
    finished_at: datetime
    actual_result: Mapping[str, str | int | float | bool | None] = field(default_factory=dict)
    evidence_ids: tuple[str, ...] = ()
    error: str | None = None
    worker_pid: int | None = None
    worker_create_time: float | None = None
    worker_return_code: int | None = None
    deduplicated: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.intent_id, str) or not self.intent_id.strip():
            raise ValueError("intent_id is required")
        if not isinstance(self.status, ExecutionStatus):
            object.__setattr__(self, "status", ExecutionStatus(self.status))
        if not isinstance(self.started_at, datetime) or not isinstance(self.finished_at, datetime):
            raise ValueError("execution timestamps must be datetime values")
        if self.started_at.tzinfo is None or self.finished_at.tzinfo is None:
            raise ValueError("execution timestamps must be timezone-aware")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        evidence_ids = tuple(self.evidence_ids)
        if any(not isinstance(item, str) or not item.strip() for item in evidence_ids):
            raise ValueError("evidence_ids must contain non-blank strings")
        object.__setattr__(self, "evidence_ids", evidence_ids)
        if self.status is ExecutionStatus.SUCCEEDED and not evidence_ids:
            raise ValueError("successful execution requires verified evidence_ids")
        copied = dict(self.actual_result)
        if any(not isinstance(key, str) or not key.strip() for key in copied):
            raise ValueError("actual_result keys must be non-blank strings")
        if any(not isinstance(value, (str, int, float, bool, type(None))) for value in copied.values()):
            raise ValueError("actual_result values must be JSON scalar types")
        if any(isinstance(value, float) and not math.isfinite(value) for value in copied.values()):
            raise ValueError("actual_result floating-point values must be finite")
        object.__setattr__(self, "actual_result", MappingProxyType(copied))
        if self.worker_pid is not None and self.worker_pid <= 0:
            raise ValueError("worker_pid must be positive")
        if self.worker_create_time is not None and (
            not math.isfinite(self.worker_create_time) or self.worker_create_time < 0
        ):
            raise ValueError("worker_create_time must be finite and non-negative")

    @classmethod
    def immediate(
        cls,
        intent_id: str,
        status: ExecutionStatus,
        *,
        error: str | None = None,
        actual_result: Mapping[str, str | int | float | bool | None] | None = None,
        evidence_ids: tuple[str, ...] = (),
        deduplicated: bool = False,
    ) -> "ExecutionResult":
        now = datetime.now(timezone.utc)
        return cls(
            intent_id=intent_id,
            status=status,
            started_at=now,
            finished_at=now,
            actual_result=actual_result or {},
            evidence_ids=evidence_ids,
            error=error,
            deduplicated=deduplicated,
        )
