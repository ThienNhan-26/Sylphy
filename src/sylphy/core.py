"""Session-aware conversation orchestration and provider contracts."""

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Event
from typing import TYPE_CHECKING, Callable, Protocol
from uuid import uuid4

if TYPE_CHECKING:
    from .context import ContextBuilder, TemporaryContext


@dataclass(frozen=True)
class Session:
    id: str
    created_at: datetime
    updated_at: datetime
    title: str | None = None


@dataclass(frozen=True)
class Message:
    id: str
    session_id: str
    role: str
    content: str
    created_at: datetime
    completed: bool = True


@dataclass(frozen=True)
class Turn:
    session_id: str
    user: Message
    assistant: Message


class ModelProvider(Protocol):
    def reply(self, messages: list[dict[str, str]], cancel: Event | None = None, on_chunk: Callable[[str], None] | None = None) -> str: ...


class Persistence(Protocol):
    def create_session(self, title: str | None = None) -> Session: ...
    def list_sessions(self) -> list[Session]: ...
    def get_session(self, session_id: str) -> Session | None: ...
    def rename_session(self, session_id: str, title: str | None) -> Session: ...
    def delete_session(self, session_id: str) -> None: ...
    def messages(self, session_id: str) -> list[Message]: ...
    def append_turn(self, user: Message, assistant: Message) -> None: ...


class ConversationError(RuntimeError):
    """Base class for recoverable conversation problems."""


class ConversationCancelled(ConversationError):
    pass


class ConversationService:
    def __init__(self, provider: ModelProvider, persistence: Persistence, context_builder: "ContextBuilder"):
        self._provider = provider
        self._persistence = persistence
        self._context_builder = context_builder

    def respond(self, session_id: str, text: str, cancel: Event | None = None,
                on_chunk: Callable[[str], None] | None = None,
                temporary_context: "TemporaryContext | None" = None) -> Turn:
        clean_text = text.strip()
        if not clean_text:
            raise ValueError("Message cannot be empty")
        if self._persistence.get_session(session_id) is None:
            raise ValueError(f"Session not found: {session_id}")
        if cancel and cancel.is_set():
            raise ConversationCancelled("Conversation cancelled")
        context = self._context_builder.build(self._persistence.messages(session_id), clean_text,
                                             temporary_context=temporary_context)
        try:
            answer = self._provider.reply(context, cancel=cancel, on_chunk=on_chunk).strip()
        except InterruptedError as exc:
            raise ConversationCancelled("Conversation cancelled") from exc
        if cancel and cancel.is_set():
            raise ConversationCancelled("Conversation cancelled")
        if not answer:
            raise ConversationError("Model returned an empty response")
        now = datetime.now(timezone.utc)
        user = Message(str(uuid4()), session_id, "user", clean_text, now)
        assistant = Message(str(uuid4()), session_id, "assistant", answer, now)
        self._persistence.append_turn(user, assistant)
        return Turn(session_id, user, assistant)
