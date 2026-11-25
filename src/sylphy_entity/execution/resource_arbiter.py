"""Atomic per-resource read/write leases; there is no global busy switch."""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import replace
from uuid import uuid4

from sylphy_entity.contracts.execution import ResourceLease, ResourceMode, ResourceRequirement


class ResourceBusy(RuntimeError):
    def __init__(self, resources: tuple[str, ...]) -> None:
        self.resources = resources
        super().__init__("resource conflict: " + ", ".join(resources))


class ResourceArbiter:
    def __init__(self, *, clock=time.monotonic) -> None:
        self._clock = clock
        self._lock = asyncio.Lock()
        self._leases: dict[str, ResourceLease] = {}

    async def acquire(
        self,
        owner_id: str,
        requirements: tuple[ResourceRequirement, ...],
        *,
        ttl_seconds: float = 30.0,
    ) -> tuple[ResourceLease, ...]:
        if not owner_id.strip() or not requirements:
            raise ValueError("owner and resource requirements are required")
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be finite and positive")
        now = self._clock()
        normalized: dict[str, ResourceMode] = {}
        for requirement in requirements:
            current = normalized.get(requirement.resource_key)
            if current is ResourceMode.EXCLUSIVE or requirement.mode is ResourceMode.EXCLUSIVE:
                normalized[requirement.resource_key] = ResourceMode.EXCLUSIVE
            else:
                normalized[requirement.resource_key] = ResourceMode.SHARED

        async with self._lock:
            self._expire_locked(now)
            conflicts: list[str] = []
            for resource, requested_mode in normalized.items():
                for lease in self._leases.values():
                    if lease.resource_key != resource:
                        continue
                    if requested_mode is ResourceMode.EXCLUSIVE or lease.mode is ResourceMode.EXCLUSIVE:
                        conflicts.append(resource)
                        break
            if conflicts:
                raise ResourceBusy(tuple(sorted(set(conflicts))))

            leases = tuple(
                ResourceLease(
                    lease_id=uuid4().hex,
                    owner_id=owner_id,
                    resource_key=resource,
                    mode=mode,
                    acquired_at_monotonic=now,
                    expires_at_monotonic=now + ttl_seconds,
                )
                for resource, mode in sorted(normalized.items())
            )
            for lease in leases:
                self._leases[lease.lease_id] = lease
            return leases

    async def renew(
        self,
        leases: tuple[ResourceLease, ...],
        *,
        ttl_seconds: float = 30.0,
    ) -> tuple[ResourceLease, ...]:
        """Atomically extend a complete set of still-active owned leases."""
        if not leases:
            raise ValueError("at least one lease is required")
        if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be finite and positive")
        now = self._clock()
        async with self._lock:
            self._expire_locked(now)
            current_items: list[ResourceLease] = []
            for lease in leases:
                current = self._leases.get(lease.lease_id)
                if current is None or current.owner_id != lease.owner_id:
                    raise ResourceBusy((lease.resource_key,))
                current_items.append(current)
            renewed = tuple(
                replace(current, expires_at_monotonic=now + ttl_seconds)
                for current in current_items
            )
            for lease in renewed:
                self._leases[lease.lease_id] = lease
            return renewed

    async def release(self, leases: tuple[ResourceLease, ...]) -> None:
        async with self._lock:
            for lease in leases:
                current = self._leases.get(lease.lease_id)
                if current is not None and current.owner_id == lease.owner_id:
                    self._leases.pop(lease.lease_id, None)

    async def snapshot(self) -> tuple[ResourceLease, ...]:
        async with self._lock:
            self._expire_locked(self._clock())
            return tuple(sorted(self._leases.values(), key=lambda item: (item.resource_key, item.lease_id)))

    def _expire_locked(self, now: float) -> None:
        expired = [key for key, lease in self._leases.items() if lease.expires_at_monotonic <= now]
        for key in expired:
            self._leases.pop(key, None)
