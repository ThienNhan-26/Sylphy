"""Bounded, in-process Presence events; this module performs no monitoring or inference."""

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import re
from threading import Lock
import unicodedata
from uuid import uuid4


class PresenceEventType(str, Enum):
    NOTICE = "notice"


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_URL_OR_PATH = re.compile(
    r"(?i)(?:\b(?:https?://|www\.)\S+|(?:^|\s)(?:[A-Za-z]:[\\/]|\\\\[^\\]+\\)|"
    r"(?:^|\s)(?:\.\.?[\\/]|[A-Za-z0-9_.-]+[\\/][A-Za-z0-9_.-]+))"
)


@dataclass(frozen=True)
class PresenceEvent:
    event_type: PresenceEventType
    title: str
    content: str
    event_id: str = field(default_factory=lambda: str(uuid4()))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source_id: str | None = None
    deduplication_key: str | None = None

    MAX_TITLE_LENGTH = 120
    MAX_CONTENT_LENGTH = 1000

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, PresenceEventType):
            raise ValueError("event_type must be a supported PresenceEventType")
        for name, value, maximum in (
            ("event_id", self.event_id, 96),
            ("title", self.title, self.MAX_TITLE_LENGTH),
            ("content", self.content, self.MAX_CONTENT_LENGTH),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            if len(value.strip()) > maximum:
                raise ValueError(f"{name} exceeds {maximum} characters")
            if any(unicodedata.category(char) in {"Cc", "Cf"} for char in value):
                raise ValueError(f"{name} cannot contain control characters")
            if name in {"title", "content"} and _URL_OR_PATH.search(value):
                raise ValueError(f"{name} cannot contain URLs or filesystem paths")
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ValueError("created_at must be a timezone-aware datetime")
        for name, value in (("source_id", self.source_id), ("deduplication_key", self.deduplication_key)):
            if value is not None and (not isinstance(value, str) or not _SAFE_IDENTIFIER.fullmatch(value)):
                raise ValueError(f"{name} must be a short opaque identifier")
        if not _SAFE_IDENTIFIER.fullmatch(self.event_id):
            raise ValueError("event_id must be a short opaque identifier")
        object.__setattr__(self, "event_id", self.event_id.strip())
        object.__setattr__(self, "title", self.title.strip())
        object.__setattr__(self, "content", self.content.strip())


class PresenceService:
    """Runtime-owned bounded FIFO with no background thread, persistence, or side effects."""

    def __init__(self, enabled: bool = False, capacity: int = 20):
        if not isinstance(enabled, bool):
            raise ValueError("Presence enabled must be a boolean")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("Presence queue capacity must be positive")
        self._enabled = enabled
        self.capacity = capacity
        self._started = False
        self._lock = Lock()
        self._pending: deque[PresenceEvent] = deque()

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    @property
    def started(self) -> bool:
        with self._lock:
            return self._started

    def set_enabled(self, enabled: bool) -> None:
        if not isinstance(enabled, bool):
            raise ValueError("Presence enabled must be a boolean")
        with self._lock:
            self._enabled = enabled

    def start(self) -> None:
        with self._lock:
            self._started = True

    def stop(self) -> None:
        with self._lock:
            self._started = False

    def publish(self, event: PresenceEvent) -> bool:
        if not isinstance(event, PresenceEvent):
            raise TypeError("PresenceService accepts only validated PresenceEvent values")
        with self._lock:
            if not self._started or not self._enabled:
                return False
            if event.deduplication_key is not None and any(
                pending.event_type == event.event_type
                and pending.source_id == event.source_id
                and pending.deduplication_key == event.deduplication_key
                for pending in self._pending
            ):
                return False
            if len(self._pending) >= self.capacity:
                return False
            self._pending.append(event)
            return True

    def pending(self) -> tuple[PresenceEvent, ...]:
        with self._lock:
            return tuple(self._pending)

    def drain_pending(self) -> tuple[PresenceEvent, ...]:
        with self._lock:
            if not self._started or not self._enabled:
                return ()
            events = tuple(self._pending)
            self._pending.clear()
            return events

    def dismiss(self, event_id: str) -> bool:
        with self._lock:
            for event in tuple(self._pending):
                if event.event_id == event_id:
                    self._pending.remove(event)
                    return True
            return False
