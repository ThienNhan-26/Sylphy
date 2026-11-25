"""Provider-neutral interface for bounded Entity cognition."""

from __future__ import annotations

import asyncio
from typing import Protocol, runtime_checkable

from sylphy_entity.contracts.cognition import CognitionOutcome, CognitionRequest


@runtime_checkable
class CognitionInterface(Protocol):
    """A replaceable capability. Implementations cannot authorize actions."""

    async def execute(
        self,
        request: CognitionRequest,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> CognitionOutcome:
        """Return a typed outcome; ordinary provider failures are not raised."""

    async def close(self) -> None:
        """Release provider-owned resources. Must be safe to call more than once."""
