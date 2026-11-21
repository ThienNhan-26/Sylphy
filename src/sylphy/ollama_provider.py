"""Minimal adapter to the local Ollama chat API."""

import logging
from threading import Event
from typing import Callable

from ollama import Client

from .core import Message

logger = logging.getLogger(__name__)


class OllamaProvider:
    def __init__(self, host: str, model: str, timeout_seconds: float = 120):
        if not model:
            raise ValueError("An Ollama model must be configured")
        self._model = model
        self._client = Client(host=host, timeout=timeout_seconds)

    def reply(self, messages: list[dict[str, str]], cancel: Event | None = None, on_chunk: Callable[[str], None] | None = None) -> str:
        try:
            response = self._client.chat(
                model=self._model,
                messages=messages,
                stream=True,
            )
            parts: list[str] = []
            for chunk in response:
                if cancel and cancel.is_set():
                    close = getattr(response, "close", None)
                    if close:
                        close()
                    raise InterruptedError("Ollama response cancelled")
                part = chunk.message.content or ""
                parts.append(part)
                if on_chunk and part:
                    on_chunk(part)
            return "".join(parts)
        except InterruptedError:
            raise
        except Exception as exc:
            logger.warning("Ollama request failed (%s)", type(exc).__name__)
            raise RuntimeError(f"Could not get a response from local Ollama model {self._model!r}") from exc
