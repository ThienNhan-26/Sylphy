"""Typed, bounded contracts for optional Entity cognition capabilities.

Cognition outputs are proposals and evidence only. They are never executable
Intents and never grant permissions.
"""

from __future__ import annotations

import json
from enum import Enum
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CognitionOperation(str, Enum):
    SUMMARIZE = "summarize"
    INTERPRET = "interpret"
    COMPARE = "compare"
    PROPOSE = "propose"


class CognitionStatus(str, Enum):
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    MODEL_OFFLINE = "model_offline"
    MODEL_UNAVAILABLE = "model_unavailable"
    INVALID_OUTPUT = "invalid_output"
    FAILED = "failed"


class CognitionRequest(BaseModel):
    """A bounded request prepared by Entity-owned policy, not by the model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str = Field(default_factory=lambda: uuid4().hex, min_length=1, max_length=128)
    operation: CognitionOperation
    purpose: str = Field(min_length=1, max_length=500)
    context: dict[str, object]
    source_refs: tuple[str, ...] = Field(default=(), max_length=32)
    max_output_tokens: int = Field(default=512, ge=32, le=1024)
    timeout_seconds: float = Field(default=30.0, gt=0.0, le=180.0)

    @field_validator("purpose")
    @classmethod
    def purpose_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("purpose must not be blank")
        return value.strip()

    @field_validator("source_refs")
    @classmethod
    def source_refs_are_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        cleaned = tuple(item.strip() for item in value)
        if any(not item for item in cleaned):
            raise ValueError("source_refs must not contain blank identifiers")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("source_refs must be unique")
        return cleaned

    @field_validator("context")
    @classmethod
    def context_is_bounded_json(cls, value: dict[str, object]) -> dict[str, object]:
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("context must contain JSON-compatible finite values") from exc
        if len(encoded.encode("utf-8")) > 32 * 1024:
            raise ValueError("context exceeds the 32 KiB cognition input limit")
        return value


class CognitionFinding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(min_length=1, max_length=80)
    statement: str = Field(min_length=1, max_length=1200)
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=16)


class CognitionProposal(BaseModel):
    """A semantic suggestion; deliberately contains no executable fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: str = Field(min_length=1, max_length=80)
    subject: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=1200)
    evidence_refs: tuple[str, ...] = Field(min_length=1, max_length=16)


class CognitionPayload(BaseModel):
    """Structured model output accepted after schema and provenance checks."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(default="", max_length=4000)
    summary_evidence_refs: tuple[str, ...] = Field(default=(), max_length=16)
    findings: tuple[CognitionFinding, ...] = Field(default=(), max_length=16)
    proposals: tuple[CognitionProposal, ...] = Field(default=(), max_length=8)

    @model_validator(mode="after")
    def summary_requires_provenance(self) -> "CognitionPayload":
        if self.summary.strip() and not self.summary_evidence_refs:
            raise ValueError("a non-empty summary requires source references")
        return self


class CognitionOutcome(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request_id: str
    status: CognitionStatus
    result: CognitionPayload | None = None
    model: str | None = None
    elapsed_ms: int = Field(ge=0)
    error_code: str | None = None
    error_message: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def result_matches_status(self) -> "CognitionOutcome":
        if self.status is CognitionStatus.COMPLETED and self.result is None:
            raise ValueError("completed cognition outcome requires a result")
        if self.status is not CognitionStatus.COMPLETED and self.result is not None:
            raise ValueError("non-completed cognition outcome must not contain a result")
        return self
