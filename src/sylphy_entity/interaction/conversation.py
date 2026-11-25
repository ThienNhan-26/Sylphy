"""Conversation ingress adapter that publishes user messages as Entity events.

This adapter deliberately does not call Cognition, retrieve Memory, generate
assistant replies, or own a session loop. Those are later integration slices.
"""

from __future__ import annotations

from typing import Protocol

from sylphy_entity.contracts.conversation import ConversationMessage
from sylphy_entity.contracts.events import EntityEvent


class EventPublisher(Protocol):
    async def publish_event(self, event: EntityEvent) -> None: ...


class ConversationIngress:
    """Translate each user message into exactly one transport-neutral event."""

    def __init__(self, publisher: EventPublisher) -> None:
        self._publisher = publisher

    async def submit(self, message: ConversationMessage) -> EntityEvent:
        event = EntityEvent(
            event_id=message.message_id,
            event_type="conversation.message.received",
            source="conversation",
            occurred_at=message.occurred_at,
            payload={
                "session_id": message.session_id,
                "message_id": message.message_id,
                "role": "user",
                "channel": message.channel,
                "text": message.text,
            },
        )
        await self._publisher.publish_event(event)
        return event
