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
from sylphy_entity.cognition.ollama import CognitionConfigurationError, OllamaCognition


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
        "findings": [
            {
                "kind": "event_interpretation",
                "statement": "The observation reports a file change.",
                "confidence": 0.95,
                "evidence_refs": ["obs-001"],
            }
        ],
        "proposals": [],
    }


@pytest.mark.asyncio
async def test_ollama_returns_typed_structured_result_and_no_tools() -> None:
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": json.dumps(success_payload())}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaCognition(model="qwen3.5", client=client)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.COMPLETED
    assert outcome.result is not None
    assert outcome.result.findings[0].evidence_refs == ("obs-001",)
    assert captured["url"].endswith("/api/chat")
    assert "tools" not in captured["body"]
    assert captured["body"]["stream"] is False
    assert captured["body"]["options"]["num_predict"] == 512
    assert captured["body"]["format"] == "json"
    assert captured["body"]["think"] is False
    assert "output_schema" not in captured["body"]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_unknown_evidence_reference_is_rejected() -> None:
    payload = success_payload()
    payload["findings"][0]["evidence_refs"] = ["invented-ref"]

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": json.dumps(payload)}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaCognition(model="qwen3.5", client=client)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.INVALID_OUTPUT
    assert outcome.result is None
    assert outcome.error_code == "unknown_evidence_reference"


@pytest.mark.asyncio
async def test_malformed_model_json_is_a_normal_invalid_output() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": "not-json"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaCognition(model="qwen3.5", client=client)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.INVALID_OUTPUT
    assert outcome.error_code == "non_json_model_content"


@pytest.mark.asyncio
async def test_fenced_json_is_normalized_but_still_schema_validated() -> None:
    content = "```json\n" + json.dumps(success_payload()) + "\n```"

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": content}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaCognition(model="lfm3-uncensored-32k:latest", client=client)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.COMPLETED
    assert outcome.result is not None
    assert outcome.result.summary == "A file changed."


@pytest.mark.asyncio
async def test_empty_content_has_specific_diagnostic_without_logging_payload() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": ""}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaCognition(model="lfm3-uncensored-32k:latest", client=client)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.INVALID_OUTPUT
    assert outcome.error_code == "empty_model_content"


@pytest.mark.asyncio
async def test_schema_mismatch_has_specific_diagnostic() -> None:
    bad_payload = {"summary": "A file changed", "summary_evidence_refs": ["obs-001"],
                   "findings": [{"kind": "x", "statement": "y", "confidence": 1.5,
                                 "evidence_refs": ["obs-001"]}], "proposals": []}

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"message": {"content": json.dumps(bad_payload)}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaCognition(model="lfm3-uncensored-32k:latest", client=client)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.INVALID_OUTPUT
    assert outcome.error_code == "cognition_schema_mismatch"


@pytest.mark.asyncio
async def test_local_ollama_connection_failure_is_model_offline() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaCognition(model="qwen3.5", client=client)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.MODEL_OFFLINE
    assert outcome.error_code == "ollama_offline"


@pytest.mark.asyncio
async def test_timeout_is_reported_without_raising() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.2)
        return httpx.Response(200, json={"message": {"content": json.dumps(success_payload())}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaCognition(model="qwen3.5", client=client)
    try:
        outcome = await provider.execute(make_request(timeout_seconds=0.02))
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.TIMED_OUT
    assert outcome.error_code == "timeout"


@pytest.mark.asyncio
async def test_explicit_cancellation_is_reported_and_transport_is_cancelled() -> None:
    started = asyncio.Event()

    async def handler(_: httpx.Request) -> httpx.Response:
        started.set()
        await asyncio.sleep(10)
        return httpx.Response(200, json={"message": {"content": json.dumps(success_payload())}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaCognition(model="qwen3.5", client=client)
    cancel_event = asyncio.Event()
    try:
        task = asyncio.create_task(provider.execute(make_request(timeout_seconds=2), cancel_event=cancel_event))
        await asyncio.wait_for(started.wait(), timeout=0.5)
        cancel_event.set()
        outcome = await asyncio.wait_for(task, timeout=0.5)
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.CANCELLED
    assert outcome.error_code == "cancelled"


@pytest.mark.asyncio
async def test_model_missing_is_reported_as_unavailable() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaCognition(model="missing-model", client=client)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.MODEL_UNAVAILABLE


@pytest.mark.asyncio
async def test_empty_evidence_insufficient_response_is_accepted() -> None:
    """An empty-but-complete JSON response is structurally valid when evidence is insufficient."""
    empty_payload = {
        "summary": "",
        "summary_evidence_refs": [],
        "findings": [],
        "proposals": [],
    }

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"message": {"content": json.dumps(empty_payload)}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaCognition(model="lfm3-uncensored-32k:latest", client=client)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.COMPLETED
    assert outcome.result is not None
    assert outcome.result.summary == ""
    assert outcome.result.summary_evidence_refs == ()
    assert outcome.result.findings == ()
    assert outcome.result.proposals == ()


@pytest.mark.asyncio
async def test_non_empty_summary_without_evidence_refs_is_rejected() -> None:
    """A non-empty summary without summary_evidence_refs fails the provenance contract."""
    bad_payload = {
        "summary": "A file changed.",
        "summary_evidence_refs": [],
        "findings": [],
        "proposals": [],
    }

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"message": {"content": json.dumps(bad_payload)}}
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaCognition(model="lfm3-uncensored-32k:latest", client=client)
    try:
        outcome = await provider.execute(make_request())
    finally:
        await provider.close()
        await client.aclose()

    assert outcome.status is CognitionStatus.INVALID_OUTPUT
    assert outcome.result is None
    assert outcome.error_code == "cognition_schema_mismatch"


@pytest.mark.asyncio
async def test_system_prompt_communicates_evidence_contract() -> None:
    """The system prompt must explicitly state the evidence obligation."""
    from sylphy_entity.cognition.ollama import _SYSTEM_PROMPT

    assert "summary_evidence_refs" in _SYSTEM_PROMPT
    assert "MUST include at least one" in _SYSTEM_PROMPT
    assert "exactly match a source_refs" in _SYSTEM_PROMPT
    assert "Never invent references" in _SYSTEM_PROMPT
    assert "evidence is insufficient" in _SYSTEM_PROMPT
    assert "CognitionPayload field structure" in _SYSTEM_PROMPT
    assert "Do not omit required top-level keys" in _SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_request_configuration_uses_json_mode_think_false_num_predict_1024_no_tools() -> None:
    """The runtime request shape stays JSON mode, no thinking, no tools, 1024-token budget."""
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": json.dumps(success_payload())}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = OllamaCognition(model="qwen3.5", client=client)
    try:
        await provider.execute(make_request(max_output_tokens=1024))
    finally:
        await provider.close()
        await client.aclose()

    body = captured["body"]
    assert body["format"] == "json"
    assert body["think"] is False
    assert body["stream"] is False
    assert body["options"]["num_predict"] == 1024
    assert "tools" not in body


def test_provider_rejects_non_loopback_endpoint() -> None:
    with pytest.raises(CognitionConfigurationError):
        OllamaCognition(model="qwen3.5", base_url="https://example.com")

    with pytest.raises(CognitionConfigurationError):
        OllamaCognition(model="qwen3.5", base_url="http://192.168.1.10:11434")


def test_cognition_request_enforces_bounds_and_json_context() -> None:
    with pytest.raises(ValueError):
        make_request(max_output_tokens=2048)

    with pytest.raises(ValueError):
        make_request(context={"not_json": object()})

    with pytest.raises(ValueError):
        make_request(context={"large": "x" * (33 * 1024)})
