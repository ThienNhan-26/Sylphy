"""Typed, provenance-first contracts for Entity long-term memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4


class MemoryOrigin(str, Enum):
    USER_CONFIRMED = "user_confirmed"
    COGNITION_CONFIRMED = "cognition_confirmed"
    IMPORTED_VERIFIED = "imported_verified"


class MemoryStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class MemoryCandidateKind(str, Enum):
    USER_STATEMENT = "user_statement"
    COGNITION_PROPOSAL = "cognition_proposal"
    EXTERNAL_IMPORT = "external_import"


class MemoryCandidateStatus(str, Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class MemoryRecord:
    """A durable semantic/experience record, never an authoritative world fact."""

    content: str
    category: str
    origin: MemoryOrigin
    source_refs: tuple[str, ...]
    memory_id: str = field(default_factory=lambda: str(uuid4()))
    confidence: float | None = None
    status: MemoryStatus = MemoryStatus.ACTIVE
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("memory content must not be empty")
        if len(self.content) > 8000:
            raise ValueError("memory content exceeds 8000 characters")
        if not self.category.strip() or len(self.category) > 80:
            raise ValueError("memory category must contain 1..80 characters")
        if not self.memory_id.strip():
            raise ValueError("memory_id must not be empty")
        if not isinstance(self.origin, MemoryOrigin):
            raise ValueError("origin must be a declared MemoryOrigin")
        if not isinstance(self.status, MemoryStatus):
            raise ValueError("status must be a declared MemoryStatus")
        if isinstance(self.source_refs, str):
            raise ValueError("source_refs must be a sequence of source identifiers")
        if any(not isinstance(ref, str) for ref in self.source_refs):
            raise ValueError("source_refs must contain only strings")
        refs = tuple(ref.strip() for ref in self.source_refs if ref.strip())
        if not refs:
            raise ValueError("memory requires at least one provenance source_ref")
        if len(refs) > 32:
            raise ValueError("memory supports at most 32 provenance source_refs")
        object.__setattr__(self, "source_refs", refs)
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("memory timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")


@dataclass(frozen=True, slots=True)
class MemoryCandidate:
    """Untrusted memory proposal awaiting an explicit confirmation decision."""

    content: str
    category: str
    kind: MemoryCandidateKind
    source_refs: tuple[str, ...]
    candidate_id: str = field(default_factory=lambda: str(uuid4()))
    confidence: float | None = None
    status: MemoryCandidateStatus = MemoryCandidateStatus.PENDING
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    decision_ref: str | None = None
    resulting_memory_id: str | None = None

    def __post_init__(self) -> None:
        if not self.content.strip() or len(self.content) > 8000:
            raise ValueError("candidate content must contain 1..8000 characters")
        if not self.category.strip() or len(self.category) > 80:
            raise ValueError("candidate category must contain 1..80 characters")
        if not self.candidate_id.strip():
            raise ValueError("candidate_id must not be empty")
        if not isinstance(self.kind, MemoryCandidateKind):
            raise ValueError("kind must be a declared MemoryCandidateKind")
        if not isinstance(self.status, MemoryCandidateStatus):
            raise ValueError("status must be a declared MemoryCandidateStatus")
        if isinstance(self.source_refs, str) or any(not isinstance(ref, str) for ref in self.source_refs):
            raise ValueError("source_refs must be a sequence of source identifiers")
        refs = tuple(ref.strip() for ref in self.source_refs if ref.strip())
        if not refs or len(refs) > 32:
            raise ValueError("candidate requires 1..32 provenance source_refs")
        if len(set(refs)) != len(refs):
            raise ValueError("candidate source_refs must be unique")
        object.__setattr__(self, "source_refs", refs)
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("candidate timestamps must be timezone-aware")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at cannot precede created_at")
        if self.status is MemoryCandidateStatus.PENDING and (self.decision_ref or self.resulting_memory_id):
            raise ValueError("pending candidate cannot have a decision or resulting memory")
        if self.status is MemoryCandidateStatus.CONFIRMED and (not self.resulting_memory_id or not self.decision_ref):
            raise ValueError("confirmed candidate requires decision_ref and resulting_memory_id")
        if self.status is MemoryCandidateStatus.REJECTED and not self.decision_ref:
            raise ValueError("rejected candidate requires decision_ref")


@dataclass(frozen=True, slots=True)
class MemoryRevision:
    """Immutable snapshot of a prior memory state and the change provenance."""

    revision_id: str
    memory_id: str
    action: str
    content: str
    category: str
    origin: MemoryOrigin
    source_refs: tuple[str, ...]
    confidence: float | None
    status: MemoryStatus
    created_at: datetime
    updated_at: datetime
    changed_at: datetime
    change_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.action not in {"corrected", "archived", "restored"}:
            raise ValueError("unsupported memory revision action")
        if not self.revision_id.strip() or not self.memory_id.strip():
            raise ValueError("revision and memory IDs must not be blank")
        if not self.change_refs or any(not item.strip() for item in self.change_refs):
            raise ValueError("memory revision requires change provenance")
        if any(item.tzinfo is None for item in (self.created_at, self.updated_at, self.changed_at)):
            raise ValueError("revision timestamps must be timezone-aware")
