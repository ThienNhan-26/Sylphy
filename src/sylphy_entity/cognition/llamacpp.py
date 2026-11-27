"""Local llama.cpp implementation of the Entity cognition interface.

Uses the OpenAI-compatible ``/v1/chat/completions`` endpoint of the same
loopback llama.cpp server that serves Conversation. The authoritative Cognition
contract, provenance gate and outcome mapping are unchanged; only the transport
and response envelope differ from the legacy Ollama adapter.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from urllib.parse import urlsplit

from pydantic import ValidationError

from sylphy_entity.contracts.cognition import (
    CognitionOutcome,
    CognitionPayload,
    CognitionRequest,
    CognitionStatus,
)
from .interface import CognitionInterface
# Shared, authoritative cognition prompt text and error vocabulary. Importing
# these constants does not select or invoke the Ollama provider.
from .ollama import (
    CognitionConfigurationError,
    CognitionDependencyError,
    _SYSTEM_PROMPT,
)

logger = logging.getLogger(__name__)

DEFAULT_LLAMACPP_URL = "http://127.0.0.1:9931"

# Cognition is a bounded metadata task: thinking is disabled even when
# Conversation has thinking enabled, and reasoning_content is never consumed.
_LLAMACPP_OUTPUT_FORMAT = {"type": "json_object"}
_THINKING_ENABLED = False


class _ExplicitCancellation(Exception):
    pass


class LlamaCppCognition(CognitionInterface):
    """Single-slot async llama.cpp adapter with bounded time and output budgets."""

    def __init__(
        self,
        *,
        model: str,
        base_url: str = DEFAULT_LLAMACPP_URL,
        client=None,
        slot: asyncio.Semaphore | None = None,
    ) -> None:
        if not model.strip():
            raise CognitionConfigurationError("llama.cpp model name must not be blank")
        parsed = urlsplit(base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise CognitionConfigurationError(
                "llama.cpp base_url must be a plain HTTP loopback URL"
            )
        if parsed.path not in {"", "/"}:
            raise CognitionConfigurationError("llama.cpp base_url must not include a path")

        try:
            import httpx
        except ImportError as exc:  # optional capability dependency
            raise CognitionDependencyError(
                "Install SYLPHY's cognition extra: pip install -e .[cognition]"
            ) from exc

        self._httpx = httpx
        self._model = model.strip()
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(180.0, connect=5.0),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )
        self._owns_client = client is None
        # When supplied by the composition root this slot is shared with the
        # Conversation generator, preventing concurrent overcommit of the one
        # llama.cpp server process.
        self._slot = slot if slot is not None else asyncio.Semaphore(1)
        self._closed = False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            await self._client.aclose()

    async def execute(
        self,
        request: CognitionRequest,
        *,
        cancel_event: asyncio.Event | None = None,
    ) -> CognitionOutcome:
        started = time.monotonic()
        if self._closed:
            return self._outcome(request, CognitionStatus.FAILED, started, "provider_closed")
        if cancel_event is not None and cancel_event.is_set():
            return self._outcome(request, CognitionStatus.CANCELLED, started, "cancelled")

        try:
            async with asyncio.timeout(request.timeout_seconds):
                await self._await_or_cancel(self._slot.acquire(), cancel_event)
                try:
                    response = await self._await_or_cancel(
                        self._client.post(
                            f"{self._base_url}/v1/chat/completions",
                            json=self._request_body(request),
                        ),
                        cancel_event,
                    )
                finally:
                    self._slot.release()
        except TimeoutError:
            return self._outcome(request, CognitionStatus.TIMED_OUT, started, "timeout")
        except _ExplicitCancellation:
            return self._outcome(request, CognitionStatus.CANCELLED, started, "cancelled")
        except asyncio.CancelledError:
            # Preserve structured task cancellation during daemon shutdown.
            raise
        except self._httpx.ConnectError:
            return self._outcome(request, CognitionStatus.MODEL_OFFLINE, started, "llamacpp_offline")
        except self._httpx.ConnectTimeout:
            return self._outcome(request, CognitionStatus.MODEL_OFFLINE, started, "llamacpp_connect_timeout")
        except self._httpx.TimeoutException:
            return self._outcome(request, CognitionStatus.TIMED_OUT, started, "http_timeout")
        except self._httpx.RequestError:
            logger.warning("llama.cpp cognition transport failed", exc_info=True)
            return self._outcome(request, CognitionStatus.MODEL_OFFLINE, started, "transport_error")
        except Exception:
            logger.exception("Unexpected llama.cpp cognition failure")
            return self._outcome(request, CognitionStatus.FAILED, started, "provider_error")

        if response.status_code == 404:
            return self._outcome(
                request, CognitionStatus.MODEL_UNAVAILABLE, started, "model_unavailable"
            )
        if response.status_code == 400:
            return self._outcome(request, CognitionStatus.FAILED, started, "request_rejected")
        if response.status_code >= 500:
            return self._outcome(request, CognitionStatus.MODEL_OFFLINE, started, "llamacpp_server_error")
        if response.status_code < 200 or response.status_code >= 300:
            return self._outcome(request, CognitionStatus.FAILED, started, "http_error")

        try:
            envelope = response.json()
        except (ValueError, TypeError):
            return self._invalid_output(request, started, "invalid_response_envelope", 0)

        choices = envelope.get("choices") if isinstance(envelope, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            return self._invalid_output(request, started, "missing_choices", 0)
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            return self._invalid_output(request, started, "missing_message", 0)
        # message.reasoning_content is deliberately ignored: internal reasoning
        # is never Cognition output and must never enter the receipt or status.
        content = message.get("content")
        if not isinstance(content, str):
            return self._invalid_output(request, started, "missing_message_content", 0)

        output_bytes = len(content.encode("utf-8"))
        finish_reason = choice.get("finish_reason")
        if finish_reason is None and isinstance(envelope, dict):
            finish_reason = envelope.get("finish_reason")
        if finish_reason == "length":
            logger.warning(
                "llama.cpp cognition output truncated request_id=%s output_bytes=%d",
                request.request_id,
                output_bytes,
            )
            return self._invalid_output(request, started, "output_truncated", output_bytes)

        normalized = self._normalize_json_content(content)
        if not normalized:
            return self._invalid_output(request, started, "empty_model_content", 0)

        try:
            raw_payload = json.loads(normalized)
        except (ValueError, TypeError):
            return self._invalid_output(request, started, "non_json_model_content", output_bytes)

        try:
            result = CognitionPayload.model_validate(raw_payload)
        except (ValidationError, TypeError, ValueError):
            return self._invalid_output(request, started, "cognition_schema_mismatch", output_bytes)

        try:
            self._validate_provenance(request, result)
        except ValueError:
            return self._invalid_output(request, started, "unknown_evidence_reference", output_bytes)

        return CognitionOutcome(
            request_id=request.request_id,
            status=CognitionStatus.COMPLETED,
            result=result,
            model=self._model,
            elapsed_ms=max(0, round((time.monotonic() - started) * 1000)),
        )

    async def _await_or_cancel(self, awaitable, cancel_event: asyncio.Event | None):
        if cancel_event is None:
            return await awaitable
        if cancel_event.is_set():
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise _ExplicitCancellation
        work = asyncio.ensure_future(awaitable)
        cancelled = asyncio.create_task(cancel_event.wait())
        try:
            done, _ = await asyncio.wait(
                {work, cancelled}, return_when=asyncio.FIRST_COMPLETED
            )
            # Prefer a completed operation if completion and cancellation race.
            if work in done:
                return await work
            if cancelled in done and cancel_event.is_set():
                raise _ExplicitCancellation
            return await work
        except BaseException:
            if not work.done():
                work.cancel()
            await asyncio.gather(work, return_exceptions=True)
            raise
        finally:
            cancelled.cancel()
            await asyncio.gather(cancelled, return_exceptions=True)

    def _request_body(self, request: CognitionRequest) -> dict[str, object]:
        user_payload = {
            "operation": request.operation.value,
            "purpose": request.purpose,
            "context": request.context,
            "source_refs": list(request.source_refs),
            "required_output_shape": {
                "summary": "string",
                "summary_evidence_refs": ["source_ref"],
                "findings": [{
                    "kind": "string",
                    "statement": "string",
                    "confidence": "number_0_to_1",
                    "evidence_refs": ["source_ref"],
                }],
                "proposals": [{
                    "kind": "string",
                    "subject": "string",
                    "rationale": "string",
                    "evidence_refs": ["source_ref"],
                }],
            },
        }
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False, allow_nan=False),
                },
            ],
            "stream": False,
            "response_format": _LLAMACPP_OUTPUT_FORMAT,
            "chat_template_kwargs": {"enable_thinking": _THINKING_ENABLED},
            "temperature": 0.0,
            "max_tokens": request.max_output_tokens,
        }

    @staticmethod
    def _normalize_json_content(content: str) -> str:
        """Accept raw JSON or one complete fenced JSON block, nothing looser."""
        text = content.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[0].strip().lower() in {"```", "```json"} and lines[-1].strip() == "```":
                text = "\n".join(lines[1:-1]).strip()
        return text

    def _invalid_output(
        self, request: CognitionRequest, started: float, error_code: str, output_bytes: int
    ) -> CognitionOutcome:
        # Never log model output or user context. The category and byte count are
        # enough to diagnose common small-model formatting failures safely.
        logger.warning(
            "llama.cpp cognition output rejected request_id=%s reason=%s output_bytes=%d",
            request.request_id,
            error_code,
            output_bytes,
        )
        return self._outcome(request, CognitionStatus.INVALID_OUTPUT, started, error_code)

    @staticmethod
    def _validate_provenance(request: CognitionRequest, result: CognitionPayload) -> None:
        allowed = set(request.source_refs)
        references = set(result.summary_evidence_refs)
        for item in result.findings:
            references.update(item.evidence_refs)
        for item in result.proposals:
            references.update(item.evidence_refs)
        if not references.issubset(allowed):
            unknown = sorted(references - allowed)
            raise ValueError(f"model cited unknown source references: {unknown}")

    def _outcome(
        self,
        request: CognitionRequest,
        status: CognitionStatus,
        started: float,
        error_code: str,
    ) -> CognitionOutcome:
        return CognitionOutcome(
            request_id=request.request_id,
            status=status,
            model=self._model,
            elapsed_ms=max(0, round((time.monotonic() - started) * 1000)),
            error_code=error_code,
        )
