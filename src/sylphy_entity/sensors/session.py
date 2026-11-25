"""Consent-gated, resource-arbitrated lifecycle for optional sensor adapters."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from enum import StrEnum
from typing import Awaitable, Callable

from sylphy_entity.contracts.observation import Observation
from sylphy_entity.contracts.sensor import SensorKind
from sylphy_entity.contracts.execution import ResourceLease, ResourceMode, ResourceRequirement
from sylphy_entity.execution.resource_arbiter import ResourceArbiter, ResourceBusy
from .consent import ConsentDenied, SensorConsentLedger


class SensorSessionState(StrEnum):
    STOPPED = "stopped"
    RUNNING = "running"


ObservationSink = Callable[[Observation], Awaitable[None]]


class SensorAdapterSession:
    """Own one sensor's exclusive device lease and gate its observations.

    This is lifecycle/policy infrastructure only. It does not open a camera,
    microphone, or any operating-system device itself.
    """

    def __init__(
        self,
        *,
        sensor: SensorKind,
        device_id: str,
        purpose: str,
        consent: SensorConsentLedger,
        arbiter: ResourceArbiter,
        sink: ObservationSink,
        max_observation_age_seconds: float = 5.0,
        lease_ttl_seconds: float = 30.0,
        lease_renew_interval_seconds: float = 10.0,
    ) -> None:
        if not device_id.strip() or not purpose.strip():
            raise ValueError("device_id and purpose must be non-blank")
        if max_observation_age_seconds <= 0:
            raise ValueError("max_observation_age_seconds must be positive")
        if lease_ttl_seconds <= 0 or lease_renew_interval_seconds <= 0:
            raise ValueError("resource lease TTL and renewal interval must be positive")
        if lease_renew_interval_seconds >= lease_ttl_seconds:
            raise ValueError("lease renewal interval must be shorter than the lease TTL")
        self.sensor = SensorKind(sensor)
        self.device_id = device_id
        self.purpose = purpose
        self._consent = consent
        self._arbiter = arbiter
        self._sink = sink
        self._max_age = max_observation_age_seconds
        self._lease_ttl = lease_ttl_seconds
        self._lease_renew_interval = lease_renew_interval_seconds
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._leases: tuple[ResourceLease, ...] = ()
        self._grant_id: str | None = None
        self._scope: str | None = None
        self._state = SensorSessionState.STOPPED

    @property
    def state(self) -> SensorSessionState:
        return self._state

    @property
    def resource_key(self) -> str:
        return f"sensor:{self.sensor.value}:{self.device_id}"

    @property
    def active_permission_scope(self) -> str | None:
        return self._scope if self._state is SensorSessionState.RUNNING else None

    async def start(self) -> None:
        if self._state is SensorSessionState.RUNNING:
            return
        grant = self._consent.require(self.sensor, self.device_id, purpose=self.purpose)
        leases = await self._arbiter.acquire(
            owner_id=f"sensor-adapter:{self.sensor.value}:{self.device_id}",
            requirements=(ResourceRequirement(self.resource_key, ResourceMode.EXCLUSIVE),),
            ttl_seconds=self._lease_ttl,
        )
        # Re-check consent after awaiting the arbiter to close the revoke/start race.
        try:
            current = self._consent.require(
                self.sensor,
                self.device_id,
                permission_scope=grant.permission_scope,
                purpose=self.purpose,
            )
        except BaseException:
            await self._arbiter.release(leases)
            raise
        self._leases = leases
        self._grant_id = current.grant_id
        self._scope = current.permission_scope
        self._state = SensorSessionState.RUNNING
        self._heartbeat_task = asyncio.create_task(
            self._renew_resource_lease(),
            name=f"sensor-lease-renew:{self.sensor.value}:{self.device_id}",
        )

    async def stop(self) -> None:
        task, self._heartbeat_task = self._heartbeat_task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        leases, self._leases = self._leases, ()
        self._state = SensorSessionState.STOPPED
        self._grant_id = None
        self._scope = None
        if leases:
            await self._arbiter.release(leases)

    async def _renew_resource_lease(self) -> None:
        try:
            while self._state is SensorSessionState.RUNNING:
                await asyncio.sleep(self._lease_renew_interval)
                grant = self._consent.require(
                    self.sensor,
                    self.device_id,
                    permission_scope=self._scope,
                    purpose=self.purpose,
                )
                if grant.grant_id != self._grant_id:
                    raise ConsentDenied("sensor consent grant changed during active session")
                self._leases = await self._arbiter.renew(
                    self._leases,
                    ttl_seconds=self._lease_ttl,
                )
        except asyncio.CancelledError:
            raise
        except (ConsentDenied, ResourceBusy):
            await self.stop()

    async def publish(self, observation: Observation) -> None:
        if self._state is not SensorSessionState.RUNNING:
            raise RuntimeError("sensor session is not running")
        try:
            grant = self._consent.require(
                self.sensor,
                self.device_id,
                permission_scope=self._scope,
                purpose=self.purpose,
            )
            if grant.grant_id != self._grant_id:
                raise ConsentDenied("sensor consent grant changed during capture")
            if observation.permission_scope != grant.permission_scope:
                raise ConsentDenied("observation permission scope does not match consent")
            if observation.source_id != f"sensor:{self.sensor.value}:{self.device_id}":
                raise ValueError("observation source_id does not match sensor session")
            now = datetime.now(timezone.utc)
            if observation.observed_at.tzinfo is None:
                raise ValueError("observation timestamp must be timezone-aware")
            age = (now - observation.observed_at).total_seconds()
            if age < 0 or age > self._max_age or observation.freshness != "current":
                raise ValueError("sensor observation is stale or has invalid clock freshness")
            # Raw media must not enter the Entity event path in this foundation slice.
            if _contains_binary(observation.payload):
                raise ValueError("raw binary media is not accepted by the sensor observation boundary")
            await self._sink(observation)
        except ConsentDenied:
            await self.stop()
            raise


def _contains_binary(value: object) -> bool:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return True
    if isinstance(value, dict):
        return any(_contains_binary(item) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(_contains_binary(item) for item in value)
    return False
