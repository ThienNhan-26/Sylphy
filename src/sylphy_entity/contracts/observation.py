"""Immutable, provenance-carrying observations emitted by world adapters."""

from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator, model_validator

from .json_value import freeze_json, thaw_json

Freshness = Literal["current", "stale", "unknown"]
Verification = Literal["confirmed", "inferred", "unverified"]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Observation(BaseModel):
    """A sensor fact, never an instruction to perform an action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    observation_id: str = Field(default_factory=lambda: str(uuid4()))
    source_id: str = Field(min_length=1, max_length=160)
    subject: str = Field(min_length=1, max_length=1024)
    kind: str = Field(min_length=1, max_length=160)
    payload: Mapping[str, Any] = Field(default_factory=dict)
    observed_at: datetime = Field(default_factory=utc_now)
    received_at: datetime = Field(default_factory=utc_now)
    sequence: int | None = Field(default=None, ge=0)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    verification: Verification = "confirmed"
    permission_scope: str = Field(min_length=1, max_length=512)
    freshness: Freshness = "current"

    @field_validator("payload", mode="after")
    @classmethod
    def freeze_payload(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        return freeze_json(value)

    @field_serializer("payload")
    def serialize_payload(self, value: Mapping[str, Any]) -> dict[str, Any]:
        return thaw_json(value)

    @field_validator("source_id", "subject", "kind", "permission_scope")
    @classmethod
    def strip_non_blank(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("value must not be blank")
        return cleaned

    @model_validator(mode="after")
    def validate_timestamps(self) -> "Observation":
        if self.observed_at.tzinfo is None or self.received_at.tzinfo is None:
            raise ValueError("observation timestamps must be timezone-aware")
        if self.received_at < self.observed_at:
            # Clocks can differ across sources. Do not reject clock skew; the
            # separate timestamps preserve what each clock reported.
            return self
        return self


class ObservationEnvelope(BaseModel):
    """Stable wrapper for transporting an Observation through Entity events."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    observation: Observation
