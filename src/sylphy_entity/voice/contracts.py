"""Typed contracts for synthesized speech and playback lifecycle."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from uuid import uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class PlaybackState(StrEnum):
    CREATED = "created"
    SYNTHESIZING = "synthesizing"
    READY = "ready"
    PLAYING = "playing"
    COMPLETED = "completed"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class VoiceEventType(StrEnum):
    SESSION_CREATED = "voice.session.created"
    SYNTHESIS_STARTED = "voice.synthesis.started"
    SYNTHESIS_COMPLETED = "voice.synthesis.completed"
    PLAYBACK_STARTED = "voice.playback.started"
    PLAYBACK_PROGRESS = "voice.playback.progress"
    PLAYBACK_COMPLETED = "voice.playback.completed"
    PLAYBACK_INTERRUPTED = "voice.playback.interrupted"
    PLAYBACK_FAILED = "voice.playback.failed"


@dataclass(frozen=True, slots=True)
class VoiceAudio:
    samples: object
    sample_rate: int
    duration_seconds: float
    mime_type: str = "audio/pcm"

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if self.duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")


@dataclass(frozen=True, slots=True)
class VoiceSession:
    text: str
    voice: str = "af_heart"
    language: str = "en-us"
    speed: float = 1.0
    session_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("text must not be blank")
        if not self.voice.strip() or not self.language.strip():
            raise ValueError("voice and language must not be blank")
        if not 0.5 <= self.speed <= 2.0:
            raise ValueError("speed must be between 0.5 and 2.0")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class VoiceEvent:
    event_type: VoiceEventType
    session_id: str
    sequence: int
    state: PlaybackState
    occurred_at: datetime = field(default_factory=utc_now)
    position_seconds: float = 0.0
    duration_seconds: float = 0.0
    amplitude: float = 0.0
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.session_id.strip():
            raise ValueError("session_id must not be blank")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if not 0.0 <= self.amplitude <= 1.0:
            raise ValueError("amplitude must be between 0 and 1")
