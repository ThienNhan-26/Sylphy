"""Typed contracts for user-originated Conversation input.

Conversation is an interaction adapter, not the Entity lifecycle or Brain authority.
A submitted user message is represented as one durable EntityEvent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import uuid4


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """One user message entering the Entity; it does not imply a reply."""

    text: str
    session_id: str
    message_id: str = field(default_factory=lambda: str(uuid4()))
    channel: str = "local"
    occurred_at: datetime = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("conversation message text must not be blank")
        if len(self.text) > 16000:
            raise ValueError("conversation message exceeds 16000 characters")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must not be blank")
        if len(self.session_id) > 128:
            raise ValueError("session_id exceeds 128 characters")
        if not isinstance(self.message_id, str) or not self.message_id.strip():
            raise ValueError("message_id must not be blank")
        if len(self.message_id) > 128:
            raise ValueError("message_id exceeds 128 characters")
        if not isinstance(self.channel, str) or not self.channel.strip() or len(self.channel) > 64:
            raise ValueError("channel must contain 1..64 characters")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
