"""Explicit, short-lived consent contracts for optional sensory adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4


class SensorKind(StrEnum):
    CAMERA = "camera"
    MICROPHONE = "microphone"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class SensorConsentGrant:
    """Ephemeral user consent for one sensor, device and purpose."""

    sensor: SensorKind
    device_id: str
    permission_scope: str
    purpose: str
    granted_by: str
    granted_at: datetime = field(default_factory=utc_now)
    expires_at: datetime | None = None
    grant_id: str = field(default_factory=lambda: uuid4().hex)

    def __post_init__(self) -> None:
        if not isinstance(self.sensor, SensorKind):
            object.__setattr__(self, "sensor", SensorKind(self.sensor))
        for name in ("device_id", "permission_scope", "purpose", "granted_by", "grant_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-blank")
            if len(value) > 256:
                raise ValueError(f"{name} exceeds 256 characters")
        if self.granted_at.tzinfo is None:
            raise ValueError("granted_at must be timezone-aware")
        if self.expires_at is not None:
            if self.expires_at.tzinfo is None:
                raise ValueError("expires_at must be timezone-aware")
            if self.expires_at <= self.granted_at:
                raise ValueError("expires_at must be later than granted_at")

    def is_active(self, *, now: datetime | None = None) -> bool:
        current = now or utc_now()
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return self.expires_at is None or current < self.expires_at
