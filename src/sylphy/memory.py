"""Small persistent-memory contract and service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


MEMORY_ORIGINS = frozenset({
    "unknown",
    "legacy_unknown",
    "user_provided",
    "manual",
    "confirmed_candidate",
})


@dataclass(frozen=True)
class Memory:
    id: str
    content: str
    category: str
    created_at: datetime
    updated_at: datetime
    source_session_id: str | None = None
    source_message_id: str | None = None
    confidence: float | None = None
    status: str = "confirmed"
    origin: str = "legacy_unknown"
    source_message_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MemoryRevision:
    """Immutable prior state and provenance for an explicit memory correction."""

    id: str
    memory_id: str
    content: str
    category: str
    created_at: datetime
    updated_at: datetime
    source_session_id: str | None
    source_message_id: str | None
    confidence: float | None
    status: str
    origin: str
    source_message_ids: tuple[str, ...]
    revision_at: datetime
    correction_origin: str
    correction_source_session_id: str | None
    correction_source_message_ids: tuple[str, ...]


class MemoryRepository(Protocol):
    def create_memory(self, content: str, category: str, source_session_id: str | None = None,
                      source_message_id: str | None = None, confidence: float | None = None,
                      origin: str = "unknown", source_message_ids: tuple[str, ...] | None = None) -> Memory: ...
    def list_memories(self, status: str | None = "confirmed") -> list[Memory]: ...
    def get_memory(self, memory_id: str) -> Memory | None: ...
    def update_memory(self, memory_id: str, content: str, category: str | None = None,
                      confidence: float | None = None, correction_origin: str = "unknown",
                      correction_source_session_id: str | None = None,
                      correction_source_message_ids: tuple[str, ...] = ()) -> Memory: ...
    def list_memory_revisions(self, memory_id: str) -> list[MemoryRevision]: ...
    def delete_memory(self, memory_id: str) -> None: ...
    def search_memories(self, query: str, limit: int = 20) -> list[Memory]: ...


class MemoryService:
    def __init__(self, repository: MemoryRepository):
        self._repository = repository

    def create(self, content: str, category: str = "other", source_session_id: str | None = None,
               source_message_id: str | None = None, confidence: float | None = None,
               origin: str = "unknown", source_message_ids: tuple[str, ...] | None = None) -> Memory:
        if not content.strip():
            raise ValueError("Memory content cannot be empty")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if origin not in MEMORY_ORIGINS:
            raise ValueError(f"Unsupported memory origin: {origin}")
        if source_message_ids is not None and any(
                not isinstance(message_id, str) or not message_id.strip()
                for message_id in source_message_ids):
            raise ValueError("source_message_ids must contain non-empty strings")
        message_ids = source_message_ids
        if message_ids is None:
            message_ids = (source_message_id,) if source_message_id else ()
        else:
            message_ids = tuple(message_ids)
        if source_message_id is None and message_ids:
            source_message_id = message_ids[-1]
        clean_content = content.strip()
        clean_category = category.strip().lower()
        if origin == "unknown" and source_message_ids is None:
            # Keep repositories implementing the original five-argument
            # contract compatible for calls that do not request new metadata.
            return self._repository.create_memory(clean_content, clean_category, source_session_id,
                                                  source_message_id, confidence)
        return self._repository.create_memory(clean_content, clean_category, source_session_id,
                                              source_message_id, confidence, origin, message_ids)

    def list(self) -> list[Memory]:
        return self._repository.list_memories()

    def get(self, memory_id: str) -> Memory:
        memory = self._repository.get_memory(memory_id)
        if memory is None:
            raise KeyError(f"Memory not found: {memory_id}")
        return memory

    def update(self, memory_id: str, content: str, category: str | None = None,
               confidence: float | None = None, correction_origin: str = "unknown",
               correction_source_session_id: str | None = None,
               correction_source_message_ids: tuple[str, ...] = ()) -> Memory:
        if not content.strip():
            raise ValueError("Memory content cannot be empty")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if correction_origin not in MEMORY_ORIGINS:
            raise ValueError(f"Unsupported memory origin: {correction_origin}")
        if any(not isinstance(message_id, str) or not message_id.strip()
               for message_id in correction_source_message_ids):
            raise ValueError("correction_source_message_ids must contain non-empty strings")
        self.get(memory_id)
        clean_category = category.strip().lower() if category else None
        if (correction_origin == "unknown" and correction_source_session_id is None
                and not correction_source_message_ids):
            return self._repository.update_memory(memory_id, content.strip(), clean_category, confidence)
        return self._repository.update_memory(memory_id, content.strip(), clean_category, confidence,
                                              correction_origin, correction_source_session_id,
                                              tuple(correction_source_message_ids))

    def history(self, memory_id: str) -> list[MemoryRevision]:
        self.get(memory_id)
        list_revisions = getattr(self._repository, "list_memory_revisions", None)
        if list_revisions is None:
            raise NotImplementedError("This memory repository does not support correction history")
        return list_revisions(memory_id)

    def delete(self, memory_id: str) -> None:
        self.get(memory_id)
        self._repository.delete_memory(memory_id)

    def search(self, query: str, limit: int = 20) -> list[Memory]:
        return self._repository.search_memories(query, limit)

    def retrieve(self, query: str, limit: int) -> list[Memory]:
        # The repository owns relevance ranking, tie-breaking, status filtering,
        # and duplicate suppression so their order and limit remain consistent.
        return self.search(query, limit)
