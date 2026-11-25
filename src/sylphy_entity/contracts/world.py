"""Immutable world facts and snapshots consumed by later Entity stages."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from .json_value import freeze_json, thaw_json
from .observation import Verification


class FactState(str, Enum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    STALE = "stale"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class WorldFact(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    subject: str
    state: FactState
    value: Mapping[str, Any] | None = None
    last_observation_id: str
    last_observed_at: datetime
    source_id: str = "unknown"
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    verification: Verification = "unverified"
    permission_scope: str = "unknown"
    last_sequence: int | None = Field(default=None, ge=0)
    updated_at: datetime = Field(default_factory=utc_now)
    reason: str | None = None

    @field_validator("value", mode="after")
    @classmethod
    def freeze_value(cls, value: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        return freeze_json(value) if value is not None else None

    @field_serializer("value")
    def serialize_value(self, value: Mapping[str, Any] | None) -> dict[str, Any] | None:
        return thaw_json(value) if value is not None else None


class WorldSnapshot(BaseModel):
    """A stable, immutable view of the current projected world."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = Field(ge=0)
    captured_at: datetime = Field(default_factory=utc_now)
    facts: tuple[WorldFact, ...] = ()
    recent_observation_ids: tuple[str, ...] = ()
    user_interaction_active: bool = False
    running_work: tuple[str, ...] = ()
    resource_pressure: float = Field(default=0.0, ge=0.0, le=1.0)
    externally_owned_resources: tuple[str, ...] = ()
    active_goal_ids: tuple[str, ...] = ()
    recent_events: tuple[str, ...] = ()

    def fact(self, subject: str) -> WorldFact | None:
        return next((fact for fact in self.facts if fact.subject == subject), None)
