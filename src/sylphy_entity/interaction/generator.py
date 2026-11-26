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
        import httpx

        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path not in {"", "/"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Conversation Ollama URL must be plain HTTP loopback")
        if not model.strip():
            raise ValueError("Conversation model must not be blank")
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


class LlamaCppConversationGenerator(ConversationGenerator):
    """OpenAI-compatible chat adapter for llama.cpp inference servers.

    POST to ``/v1/chat/completions`` with ``stream: false``.
    Parses ``choices[0].message.content`` from the JSON response.
    Enforces plain HTTP loopback URLs, consistent with the Ollama adapter.
    No tools or function calling support.

    Gemma thinking is requested via ``chat_template_kwargs.enable_thinking``.
    ``message.reasoning_content`` is never read: it is an internal model
    artifact and must not become conversational content.
    """

    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://127.0.0.1:9931",
        client: httpx.AsyncClient | None = None,
        thinking_enabled: bool = True,
    ):
        import httpx

        parsed = urlsplit(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.path not in {"", "/"} or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Conversation llama.cpp URL must be plain HTTP loopback")
        if not model.strip():
            raise ValueError("Conversation model must not be blank")
        self._httpx = httpx
        self._model = model.strip()
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=httpx.Timeout(90.0, connect=5.0), limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))
        self._owns_client = client is None
        self._thinking_enabled = thinking_enabled
        self._slot = asyncio.Semaphore(1)
        self._closed = False

    async def generate(self, *, messages: tuple[ConversationTurn, ...], timeout_seconds: float = 90.0) -> str:
        if self._closed:
            raise RuntimeError("conversation provider is closed")
        if not messages or len(messages) > 24:
            raise ValueError("conversation context must contain 1..24 messages")
        # Build payload mirroring Ollama payload shape while using OpenAI paths.
        # ConversationTurn carries role + content; system messages remain first.
        payload = [
            {"role": m.role, "content": m.content[:16000]}
            for m in messages
        ]
        async with asyncio.timeout(timeout_seconds):
            async with self._slot:
                response = await self._client.post(
                    f"{self._base_url}/v1/chat/completions",
                    json={
                        "model": self._model,
                        "messages": payload,
                        "stream": False,
                        "chat_template_kwargs": {
                            "enable_thinking": self._thinking_enabled,
                        },
                    },
                )
        if response.status_code == 404:
            raise RuntimeError("conversation_model_unavailable")
        response.raise_for_status()
        envelope = response.json()
        # OpenAI-shape: choices[0].message.content
        choices = envelope.get("choices", [])
        if not choices or not isinstance(choices, list) or len(choices) == 0:
            raise RuntimeError("conversation_invalid_output")
        message = choices[0].get("message", {}) if isinstance(choices[0], dict) else None
        if not isinstance(message, dict):
            raise RuntimeError("conversation_invalid_output")
        content = message.get("content")
        # ``message.reasoning_content`` is intentionally ignored: reasoning is an
        # internal generation artifact, never conversation content or a fallback.
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("conversation_invalid_output")
        # Truncation guard consistent with Ollama adapter.
        if envelope.get("finish_reason") == "length":
            raise RuntimeError("conversation_output_truncated")
        return content.strip()[:12000]

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()