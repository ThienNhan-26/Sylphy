"""Bounded local conversation generation, separate from structured Cognition."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True, slots=True)
class ConversationTurn:
    role: str
    content: str


class ConversationGenerator:
    async def generate(self, *, messages: tuple[ConversationTurn, ...], timeout_seconds: float = 90.0) -> str:
        raise NotImplementedError

    async def close(self) -> None:
        return None


class OllamaConversationGenerator(ConversationGenerator):
    """Plain-text chat adapter; it has no tools and cannot create Runtime Intents."""
    def __init__(self, *, model: str, base_url: str = "http://127.0.0.1:11434", client=None):
        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path not in {"", "/"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Conversation Ollama URL must be plain HTTP loopback")
        if not model.strip():
            raise ValueError("Conversation model must not be blank")
        import httpx
        self._httpx = httpx
        self._model = model.strip()
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0), limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))
        self._owns_client = client is None
        self._slot = asyncio.Semaphore(1)
        self._closed = False

    async def generate(self, *, messages: tuple[ConversationTurn, ...], timeout_seconds: float = 90.0) -> str:
        if self._closed:
            raise RuntimeError("conversation provider is closed")
        if not messages or len(messages) > 24:
            raise ValueError("conversation context must contain 1..24 messages")
        payload = [{"role": m.role, "content": m.content[:16000]} for m in messages]
        async with asyncio.timeout(timeout_seconds):
            async with self._slot:
                response = await self._client.post(f"{self._base_url}/api/chat", json={"model": self._model, "messages": payload, "stream": False, "keep_alive": "5m", "options": {"num_predict": 768}})
        if response.status_code == 404:
            raise RuntimeError("conversation_model_unavailable")
        response.raise_for_status()
        envelope = response.json()
        content = envelope.get("message", {}).get("content") if isinstance(envelope, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("conversation_invalid_output")
        if envelope.get("done_reason") == "length":
            raise RuntimeError("conversation_output_truncated")
        return content.strip()[:12000]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()
