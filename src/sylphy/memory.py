"""Small persistent-memory contract and service."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


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


class MemoryRepository(Protocol):
    def create_memory(self, content: str, category: str, source_session_id: str | None = None,
                      source_message_id: str | None = None, confidence: float | None = None) -> Memory: ...
    def list_memories(self, status: str | None = "confirmed") -> list[Memory]: ...
    def get_memory(self, memory_id: str) -> Memory | None: ...
    def update_memory(self, memory_id: str, content: str, category: str | None = None,
                      confidence: float | None = None) -> Memory: ...
    def delete_memory(self, memory_id: str) -> None: ...
    def search_memories(self, query: str, limit: int = 20) -> list[Memory]: ...


class MemoryService:
    def __init__(self, repository: MemoryRepository):
        self._repository = repository

    def create(self, content: str, category: str = "other", source_session_id: str | None = None,
               source_message_id: str | None = None, confidence: float | None = None) -> Memory:
        if not content.strip():
            raise ValueError("Memory content cannot be empty")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        return self._repository.create_memory(content.strip(), category.strip().lower(), source_session_id,
                                              source_message_id, confidence)

    def list(self) -> list[Memory]:
        return self._repository.list_memories()

    def get(self, memory_id: str) -> Memory:
        memory = self._repository.get_memory(memory_id)
        if memory is None:
            raise KeyError(f"Memory not found: {memory_id}")
        return memory

    def update(self, memory_id: str, content: str, category: str | None = None,
               confidence: float | None = None) -> Memory:
        if not content.strip():
            raise ValueError("Memory content cannot be empty")
        if confidence is not None and not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        self.get(memory_id)
        return self._repository.update_memory(memory_id, content.strip(), category.strip().lower() if category else None,
                                              confidence)

    def delete(self, memory_id: str) -> None:
        self.get(memory_id)
        self._repository.delete_memory(memory_id)

    def search(self, query: str, limit: int = 20) -> list[Memory]:
        return self._repository.search_memories(query, limit)

    def retrieve(self, query: str, limit: int) -> list[Memory]:
        return self.search(query, limit)
