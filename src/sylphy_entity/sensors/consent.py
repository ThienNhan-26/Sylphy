"""In-memory, fail-closed consent authority for camera and microphone adapters."""

from __future__ import annotations

from datetime import datetime

from sylphy_entity.contracts.sensor import SensorConsentGrant, SensorKind, utc_now


class ConsentDenied(PermissionError):
    pass


class SensorConsentLedger:
    """Tracks explicit grants for the current daemon lifetime only.

    Consent is intentionally not restored across daemon restarts in this first
    slice. A restart therefore returns every sensor to the denied-by-default
    state until the user grants consent again.
    """

    def __init__(self) -> None:
        self._grants: dict[tuple[SensorKind, str], SensorConsentGrant] = {}
        self._revoked: set[str] = set()

    def grant(self, grant: SensorConsentGrant) -> None:
        if not isinstance(grant, SensorConsentGrant):
            raise TypeError("grant must be a SensorConsentGrant")
        if not grant.is_active():
            raise ConsentDenied("expired sensor consent cannot be installed")
        key = (grant.sensor, grant.device_id)
        previous = self._grants.get(key)
        if previous is not None:
            self._revoked.add(previous.grant_id)
        self._grants[key] = grant

    def revoke(self, sensor: SensorKind, device_id: str) -> bool:
        key = (SensorKind(sensor), device_id)
        current = self._grants.pop(key, None)
        if current is None:
            return False
        self._revoked.add(current.grant_id)
        return True

    def require(
        self,
        sensor: SensorKind,
        device_id: str,
        *,
        permission_scope: str | None = None,
        purpose: str | None = None,
        now: datetime | None = None,
    ) -> SensorConsentGrant:
        key = (SensorKind(sensor), device_id)
        grant = self._grants.get(key)
        if grant is None or grant.grant_id in self._revoked:
            raise ConsentDenied(f"no active consent for {key[0].value}:{device_id}")
        if not grant.is_active(now=now or utc_now()):
            self.revoke(*key)
            raise ConsentDenied(f"consent expired for {key[0].value}:{device_id}")
        if permission_scope is not None and grant.permission_scope != permission_scope:
            raise ConsentDenied("sensor consent scope mismatch")
        if purpose is not None and grant.purpose != purpose:
            raise ConsentDenied("sensor consent purpose mismatch")
        return grant

    def snapshot(self) -> tuple[SensorConsentGrant, ...]:
        return tuple(self._grants.values())
