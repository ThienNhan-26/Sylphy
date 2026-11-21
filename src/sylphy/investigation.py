"""Durable, bounded investigation lifecycle contracts (no research execution)."""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from uuid import uuid4


class InvestigationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class InvestigationBudgetError(RuntimeError):
    """An investigation attempted to exceed one of its persisted limits."""


class InvestigationExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class InvestigationBudget:
    """Finite limits captured on each investigation for future execution."""

    max_duration_seconds: int = 900
    max_model_calls: int = 3
    max_search_requests: int = 3
    max_pages: int = 5
    max_source_bytes: int = 2_000_000
    max_extracted_text_chars: int = 12_000

    MAXIMA = {
        "max_duration_seconds": 86_400,
        "max_model_calls": 100,
        "max_search_requests": 100,
        "max_pages": 100,
        "max_source_bytes": 50_000_000,
        "max_extracted_text_chars": 1_000_000,
    }

    def __post_init__(self) -> None:
        for name, value in self.as_dict().items():
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= self.MAXIMA[name]:
                raise ValueError(f"{name} must be an integer between 1 and {self.MAXIMA[name]}")

    def as_dict(self) -> dict[str, int]:
        return {
            "max_duration_seconds": self.max_duration_seconds,
            "max_model_calls": self.max_model_calls,
            "max_search_requests": self.max_search_requests,
            "max_pages": self.max_pages,
            "max_source_bytes": self.max_source_bytes,
            "max_extracted_text_chars": self.max_extracted_text_chars,
        }


_TRANSITIONS = {
    InvestigationStatus.QUEUED: {InvestigationStatus.RUNNING, InvestigationStatus.CANCELLED,
                                 InvestigationStatus.EXPIRED},
    InvestigationStatus.RUNNING: {InvestigationStatus.QUEUED, InvestigationStatus.COMPLETED,
                                  InvestigationStatus.FAILED, InvestigationStatus.CANCELLED,
                                  InvestigationStatus.EXPIRED},
}


@dataclass(frozen=True)
class Investigation:
    id: str
    source_question_id: str
    question: str
    status: InvestigationStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    attempt_count: int
    max_attempts: int
    budget: InvestigationBudget
    last_error: str | None = None
    deferred_at: datetime | None = None
    search_calls_used: int = 0
    pages_read: int = 0
    model_calls_used: int = 0
    source_bytes_used: int = 0
    extracted_chars_used: int = 0
    started_at: datetime | None = None

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.id, self.source_question_id, self.question)):
            raise ValueError("Investigation ID, source question ID, and question are required")
        if not isinstance(self.status, InvestigationStatus):
            raise ValueError("Unsupported investigation status")
        for value in (self.created_at, self.updated_at, self.expires_at):
            if not isinstance(value, datetime) or value.tzinfo is None:
                raise ValueError("Investigation timestamps must be timezone-aware")
        if self.updated_at < self.created_at or self.expires_at <= self.created_at:
            raise ValueError("Investigation timestamps are inconsistent")
        if (isinstance(self.max_attempts, bool) or not isinstance(self.max_attempts, int)
                or not 1 <= self.max_attempts <= 10):
            raise ValueError("max_attempts must be an integer between 1 and 10")
        if (isinstance(self.attempt_count, bool) or not isinstance(self.attempt_count, int)
                or not 0 <= self.attempt_count <= self.max_attempts):
            raise ValueError("attempt_count must be between zero and max_attempts")
        if not isinstance(self.budget, InvestigationBudget):
            raise ValueError("budget must be an InvestigationBudget")
        if self.deferred_at is not None and self.deferred_at.tzinfo is None:
            raise ValueError("deferred_at must be timezone-aware")
        if self.started_at is not None and self.started_at.tzinfo is None:
            raise ValueError("started_at must be timezone-aware")
        for name, used, maximum in (
            ("search_calls_used", self.search_calls_used, self.budget.max_search_requests),
            ("pages_read", self.pages_read, self.budget.max_pages),
            ("model_calls_used", self.model_calls_used, self.budget.max_model_calls),
            ("source_bytes_used", self.source_bytes_used, self.budget.max_source_bytes),
            ("extracted_chars_used", self.extracted_chars_used, self.budget.max_extracted_text_chars),
        ):
            if isinstance(used, bool) or not isinstance(used, int) or not 0 <= used <= maximum:
                raise ValueError(f"{name} must be between zero and its persisted budget")

    @classmethod
    def queued(cls, source_question_id: str, question: str, budget: InvestigationBudget,
               max_attempts: int = 3, now: datetime | None = None) -> "Investigation":
        created = now or datetime.now().astimezone()
        if created.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return cls(str(uuid4()), source_question_id, question, InvestigationStatus.QUEUED,
                   created, created, created + timedelta(seconds=budget.max_duration_seconds),
                   0, max_attempts, budget)

    def transition(self, target: InvestigationStatus, now: datetime | None = None,
                   error: str | None = None) -> "Investigation":
        if not isinstance(target, InvestigationStatus) or target not in _TRANSITIONS.get(self.status, set()):
            raise ValueError(f"Invalid investigation transition: {self.status.value} -> {getattr(target, 'value', target)}")
        changed_at = now or datetime.now().astimezone()
        if changed_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        if target == InvestigationStatus.RUNNING and changed_at >= self.expires_at:
            raise ValueError("Investigation has expired")
        if target == InvestigationStatus.RUNNING and self.attempt_count >= self.max_attempts:
            raise ValueError("Investigation has reached its attempt limit")
        if target == InvestigationStatus.FAILED and not (isinstance(error, str) and error.strip()):
            raise ValueError("A failed investigation requires error information")
        return Investigation(
            self.id, self.source_question_id, self.question, target, self.created_at, changed_at,
            self.expires_at, self.attempt_count + (target == InvestigationStatus.RUNNING),
            self.max_attempts, self.budget, error if target == InvestigationStatus.FAILED else None,
            changed_at if target == InvestigationStatus.QUEUED else self.deferred_at,
            self.search_calls_used, self.pages_read, self.model_calls_used,
            self.source_bytes_used, self.extracted_chars_used,
            changed_at if target == InvestigationStatus.RUNNING else self.started_at,
        )


@dataclass(frozen=True)
class InvestigationEvidence:
    id: str
    investigation_id: str
    source_url: str
    provider: str
    search_rank: int
    title: str
    snippet: str
    status: str
    selected_at: datetime
    retrieved_at: datetime | None = None
    final_url: str | None = None
    content_type: str | None = None
    text: str = ""
    content_hash: str | None = None
    source_bytes: int = 0
    error: str | None = None

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.id, self.investigation_id, self.source_url, self.provider)):
            raise ValueError("Evidence identifiers, URL, and provider are required")
        if self.status not in {"selected", "retrieved", "failed"}:
            raise ValueError("Unsupported evidence retrieval status")
        if isinstance(self.search_rank, bool) or not isinstance(self.search_rank, int) or self.search_rank < 1:
            raise ValueError("search_rank must be positive")
        if any(not isinstance(value, str) for value in (self.title, self.snippet, self.text)):
            raise ValueError("Evidence text fields must be strings")
        if not isinstance(self.selected_at, datetime) or self.selected_at.tzinfo is None:
            raise ValueError("selected_at must be timezone-aware")
        if self.retrieved_at is not None and self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")
        if isinstance(self.source_bytes, bool) or not isinstance(self.source_bytes, int) or self.source_bytes < 0:
            raise ValueError("source_bytes cannot be negative")
        if self.status == "retrieved" and (self.retrieved_at is None or not self.final_url or not self.content_hash):
            raise ValueError("Retrieved evidence requires retrieval metadata and content hash")


@dataclass(frozen=True)
class InvestigationFinding:
    text: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class InvestigationResult:
    investigation_id: str
    summary: str
    findings: tuple[InvestigationFinding, ...]
    uncertainty: str
    limitations: tuple[str, ...]
    evidence_sufficient: bool
    created_at: datetime


