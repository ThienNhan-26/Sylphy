"""Transport-neutral contracts for Avatar presentation facts and claims."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AvatarEventKind(StrEnum):
    ENTITY_STARTED = "entity.started"
    ENTITY_STATE_CHANGED = "entity.state_changed"
    COGNITION_STARTED = "cognition.requested"
    COGNITION_COMPLETED = "cognition.result"
    CONVERSATION_MESSAGE_RECEIVED = "conversation.message.received"
    VOICE_PLAYBACK_STARTED = "voice.playback.started"
    VOICE_PLAYBACK_PROGRESS = "voice.playback.progress"
    VOICE_PLAYBACK_COMPLETED = "voice.playback.completed"
    VOICE_PLAYBACK_INTERRUPTED = "voice.playback.interrupted"
    VOICE_PLAYBACK_FAILED = "voice.playback.failed"
    ENTITY_PAUSED = "entity.paused"
    ENTITY_RESUMED = "entity.resumed"
    ENTITY_STOPPED = "entity.stopped"


class AvatarBehavior(StrEnum):
    IDLE = "idle"
    PROCESSING = "processing"
    REACTING = "reacting"
    SPEAKING = "speaking"
    INTERRUPTED = "interrupted"
    PAUSED = "paused"
    SHUTDOWN = "shutdown"


class BodyRegion(StrEnum):
    BODY = "body"
    HEAD = "head"
    EYES = "eyes"
    FACE = "face"
    MOUTH = "mouth"


@dataclass(frozen=True, slots=True)
class AvatarEvent:
    kind: AvatarEventKind
    source: str
    event_id: str = field(default_factory=lambda: str(uuid4()))
    session_id: str | None = None
    sequence: int = 0
    occurred_at: datetime = field(default_factory=utc_now)
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source.strip():
            raise ValueError("source must not be blank")
        if not self.event_id.strip():
            raise ValueError("event_id must not be blank")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class BehaviorClaim:
    behavior: AvatarBehavior
    owner: str
    regions: tuple[BodyRegion, ...]
    priority: int | None = None
    session_id: str | None = None
    sequence: int = 0

    def __post_init__(self) -> None:
        if not self.owner.strip():
            raise ValueError("owner must not be blank")
        if not self.regions:
            raise ValueError("a behavior claim must own at least one body region")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
