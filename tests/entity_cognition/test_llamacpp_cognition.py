"""LlamaCppCognition adapter tests (P3-2A).

All transport is faked; no live llama.cpp server or model download is required.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from sylphy_entity.contracts.cognition import (
    CognitionOperation,
    CognitionRequest,
    CognitionStatus,
)
from sylphy_entity.cognition.llamacpp import (
    CognitionConfigurationError,
    LlamaCppCognition,
)


def make_request(**overrides) -> CognitionRequest:
    data = {
        "operation": CognitionOperation.INTERPRET,
        "purpose": "Interpret the supplied event evidence.",
        "context": {"event": "file_changed", "metadata": {"size_bytes": 12}},
        "source_refs": ("obs-001",),
        "timeout_seconds": 1.0,
    }
    data.update(overrides)
    return CognitionRequest(**data)


def success_payload() -> dict:
    return {
        "summary": "A file changed.",
        "summary_evidence_refs": ["obs-001"],
        "findings": [{
            "kind": "event_interpretation",
            "statement": "The observation reports a file change.",
            "confidence": 0.95,
            "evidence_refs": ["obs-001"],
        }],
        "proposals": [],
    }


def openai_response(content: str, **choice_extras) -> httpx.Response:
    choice = {"message": {"role": "assistant", "content": content}}
    choice.update(choice_extras)
    return httpx.Response(200, json={"choices": [choice]})


def build_provider(handler) -> tuple[LlamaCppCognition, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LlamaCppCognition(model="gemma-4-E2B-it-Artemis-Q3_K_M.gguf", client=client), client


@pytest.mark.asyncio
async def test_request_uses_openai_endpoint_structured_json_no_tools() -> None:
    captured: dict = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return openai_response(json.dumps(success_payload()))

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.COMPLETED
    assert captured["url"] == "http://127.0.0.1:9931/v1/chat/completions"
    body = captured["body"]
    assert "tools" not in body
    assert body["stream"] is False
    assert body["response_format"] == {"type": "json_object"}
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0.0
    assert body["max_tokens"] == 512
    assert body["model"] == "gemma-4-E2B-it-Artemis-Q3_K_M.gguf"
    assert "summary_evidence_refs" in body["messages"][1]["content"]


@pytest.mark.asyncio
async def test_structured_result_is_typed_and_bounded() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return openai_response(json.dumps(success_payload()))

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.COMPLETED
    assert outcome.result is not None
    assert outcome.result.summary_evidence_refs == ("obs-001",)
    assert outcome.result.findings[0].evidence_refs == ("obs-001",)
    assert outcome.result.proposals == ()
    assert outcome.model == "gemma-4-E2B-it-Artemis-Q3_K_M.gguf"


@pytest.mark.asyncio
async def test_unknown_evidence_reference_is_rejected() -> None:
    payload = success_payload()
    payload["findings"][0]["evidence_refs"] = ["invented-ref"]

    async def handler(_: httpx.Request) -> httpx.Response:
        return openai_response(json.dumps(payload))

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.INVALID_OUTPUT
    assert outcome.result is None
    assert outcome.error_code == "unknown_evidence_reference"


@pytest.mark.asyncio
async def test_non_empty_summary_without_provenance_is_rejected() -> None:
    payload = success_payload()
    payload["summary_evidence_refs"] = []
    payload["findings"] = []

    async def handler(_: httpx.Request) -> httpx.Response:
        return openai_response(json.dumps(payload))

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.INVALID_OUTPUT
    assert outcome.error_code == "cognition_schema_mismatch"


@pytest.mark.asyncio
async def test_empty_but_complete_response_is_accepted() -> None:
    payload = {
        "summary": "", "summary_evidence_refs": [], "findings": [], "proposals": [],
    }

    async def handler(_: httpx.Request) -> httpx.Response:
        return openai_response(json.dumps(payload))

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.COMPLETED
    assert outcome.result is not None
    assert outcome.result.summary == ""


@pytest.mark.asyncio
async def test_malformed_json_is_invalid_output() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return openai_response("not-json")

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.INVALID_OUTPUT
    assert outcome.error_code == "non_json_model_content"


@pytest.mark.asyncio
async def test_fenced_json_is_normalized_but_validated() -> None:
    content = "```json\n" + json.dumps(success_payload()) + "\n```"

    async def handler(_: httpx.Request) -> httpx.Response:
        return openai_response(content)

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.COMPLETED


@pytest.mark.asyncio
async def test_empty_content_is_rejected() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return openai_response("")

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.INVALID_OUTPUT
    assert outcome.error_code == "empty_model_content"


@pytest.mark.asyncio
async def test_schema_mismatch_is_rejected() -> None:
    invalid = {
        "summary": "x",
        "summary_evidence_refs": ["obs-001"],
        "findings": [{
            "kind": "k", "statement": "s", "confidence": 1.5,
            "evidence_refs": ["obs-001"],
        }],
    }

    async def handler(_: httpx.Request) -> httpx.Response:
        return openai_response(json.dumps(invalid))

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.INVALID_OUTPUT
    assert outcome.error_code == "cognition_schema_mismatch"


@pytest.mark.asyncio
async def test_truncated_output_is_rejected() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return openai_response(json.dumps(success_payload()), finish_reason="length")

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.INVALID_OUTPUT
    assert outcome.error_code == "output_truncated"


@pytest.mark.asyncio
async def test_missing_choices_is_rejected() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": []})

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.INVALID_OUTPUT
    assert outcome.error_code == "missing_choices"


@pytest.mark.asyncio
async def test_reasoning_content_is_never_used_as_output() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{
            "message": {
                "content": json.dumps(success_payload()),
                "reasoning_content": "INTERNAL-REASONING-MUST-NOT-LEAK",
            }
        }]})

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.COMPLETED
    assert outcome.result is not None
    assert "INTERNAL-REASONING-MUST-NOT-LEAK" not in outcome.result.summary
    assert all(
        "INTERNAL-REASONING-MUST-NOT-LEAK" not in item.statement
        for item in outcome.result.findings
    )


@pytest.mark.asyncio
async def test_offline_server_is_model_offline() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline")

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.MODEL_OFFLINE
    assert outcome.error_code == "llamacpp_offline"


@pytest.mark.asyncio
async def test_unexpected_internal_exception_is_provider_error() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        raise RuntimeError("unexpected adapter failure")

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.FAILED
    assert outcome.error_code == "provider_error"
    assert outcome.result is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "expected_status", "expected_code"),
    [
        (404, CognitionStatus.MODEL_UNAVAILABLE, "model_unavailable"),
        (400, CognitionStatus.FAILED, "request_rejected"),
        (500, CognitionStatus.MODEL_OFFLINE, "llamacpp_server_error"),
        (302, CognitionStatus.FAILED, "http_error"),
    ],
)
async def test_http_error_classes(status_code, expected_status, expected_code) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json={})

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is expected_status
    assert outcome.error_code == expected_code


@pytest.mark.asyncio
async def test_timeout_is_reported_without_raising() -> None:
    started = asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.sleep(0.5)
        return openai_response(json.dumps(success_payload()))

    provider, client = build_provider(handler)
    try:
        outcome = await provider.execute(make_request(timeout_seconds=0.02))
        assert started.is_set()
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.TIMED_OUT
    assert outcome.error_code == "timeout"


@pytest.mark.asyncio
async def test_cancel_event_cancels_transport_and_reports_cancelled() -> None:
    started = asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.sleep(2.0)
        return openai_response(json.dumps(success_payload()))

    provider, client = build_provider(handler)
    cancel_event = asyncio.Event()
    try:
        task = asyncio.create_task(
            provider.execute(make_request(timeout_seconds=3.0), cancel_event=cancel_event)
        )
        await asyncio.wait_for(started.wait(), timeout=0.5)
        cancel_event.set()
        outcome = await asyncio.wait_for(task, timeout=0.5)
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.CANCELLED
    assert outcome.error_code == "cancelled"


@pytest.mark.asyncio
async def test_close_is_idempotent() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return openai_response(json.dumps(success_payload()))

    provider, client = build_provider(handler)
    try:
        await provider.close()
        await provider.close()
        outcome = await provider.execute(make_request())
    finally:
        await client.aclose()

    assert outcome.status is CognitionStatus.FAILED
    assert outcome.error_code == "provider_closed"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.com:9931",
        "https://127.0.0.1:9931",
        "http://127.0.0.1:9931/path",
        "http://user:pass@127.0.0.1:9931",
    ],
)
def test_non_loopback_or_non_plain_urls_are_rejected(base_url) -> None:
    with pytest.raises(CognitionConfigurationError):
        LlamaCppCognition(model="x", base_url=base_url)


def test_request_bounds_are_enforced() -> None:
    with pytest.raises(ValueError):
        make_request(purpose="   ")
    with pytest.raises(ValueError):
        make_request(source_refs=("a", "a"))
    with pytest.raises(ValueError):
        make_request(max_output_tokens=2048)
    with pytest.raises(ValueError):
        make_request(timeout_seconds=200.0)
