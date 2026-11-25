"""Shared adapter interface for live and simulated observation sources."""

from __future__ import annotations

from typing import Awaitable, Callable, Protocol

from sylphy_entity.contracts.observation import Observation

ObservationSink = Callable[[Observation], Awaitable[None]]


class WorldAdapter(Protocol):
    @property
    def adapter_id(self) -> str: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    def status(self) -> dict[str, object]: ...
