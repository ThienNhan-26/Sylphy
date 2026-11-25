"""Bridge Voice Output lifecycle facts into the existing Entity event ingress."""
from __future__ import annotations
from typing import Protocol
from sylphy_entity.contracts.events import EntityEvent
from .contracts import VoiceEvent, VoiceSession
from .runtime import VoiceRuntime


class EntityEventPublisher(Protocol):
    async def publish_event(self, event: EntityEvent) -> None: ...


class VoiceCapability:
    """Voice facade; it owns no Entity state and creates no secondary event bus."""
    def __init__(self, runtime: VoiceRuntime, publisher: EntityEventPublisher):
        self.runtime = runtime
        self.publisher = publisher
        # Install the bridge at the Voice boundary so every lifecycle fact is
        # persisted and projected by the existing Entity EventStore/EventLoop.
        self.runtime.event_sink = self._publish_voice_event

    async def _publish_voice_event(self, voice_event: VoiceEvent) -> None:
        await self.publisher.publish_event(EntityEvent(
            event_type=voice_event.event_type.value,
            source="voice-output",
            occurred_at=voice_event.occurred_at,
            payload={
                "session_id": voice_event.session_id,
                "sequence": voice_event.sequence,
                "state": voice_event.state.value,
                "position_seconds": voice_event.position_seconds,
                "duration_seconds": voice_event.duration_seconds,
                "amplitude": voice_event.amplitude,
                "reason": voice_event.reason,
            },
        ))

    async def speak(self, session: VoiceSession) -> None:
        await self.runtime.speak(session)

    async def stop(self, reason: str = "user_requested") -> None:
        await self.runtime.stop(reason)

    def status(self) -> dict[str, object]:
        return self.runtime.status()
