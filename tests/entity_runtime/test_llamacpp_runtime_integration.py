"""P3-2 runtime composition and shared-resource integration tests."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from sylphy_entity import daemon as daemon_module
from sylphy_entity.cognition.llamacpp import LlamaCppCognition
from sylphy_entity.interaction.generator import (
    ConversationTurn,
    LlamaCppConversationGenerator,
    OllamaConversationGenerator,
)
from sylphy_entity.cognition.ollama import OllamaCognition


class RecordingSupervisor:
    instances: list = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        RecordingSupervisor.instances.append(self)

    async def start(self) -> None:
        return None

    async def serve_forever(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset():
    RecordingSupervisor.instances.clear()
    yield
    RecordingSupervisor.instances.clear()


def run_daemon(monkeypatch, argv: list[str], tmp_path: Path) -> RecordingSupervisor:
    monkeypatch.setattr(daemon_module, "EntitySupervisor", RecordingSupervisor)
    monkeypatch.setattr(
        daemon_module, "load_entity_config",
        lambda: SimpleNamespace(identity_file=None),
    )
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.chdir(tmp_path)
    daemon_module.main()
    return RecordingSupervisor.instances[0]


def test_default_runtime_selects_llama_cpp_for_both_capabilities(monkeypatch, tmp_path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    supervisor = run_daemon(monkeypatch, [
        "sylphy-entity", "--port", "0",
        "--autonomy", "--sandbox-root", str(sandbox),
    ], tmp_path)
    generator = supervisor.kwargs["conversation_generator"]
    cognition = supervisor.kwargs["cognition_provider"]
    try:
        assert isinstance(generator, LlamaCppConversationGenerator)
        assert isinstance(cognition, LlamaCppCognition)
        assert generator._model == "gemma-4-E2B-it-Artemis-Q3_K_M.gguf"
        assert cognition._model == generator._model
        assert generator._base_url == "http://127.0.0.1:9931"
        assert cognition._base_url == generator._base_url
        assert not isinstance(generator, OllamaConversationGenerator)
        assert not isinstance(cognition, OllamaCognition)
        # One llama.cpp process => one shared inference slot.
        assert generator._slot is cognition._slot
    finally:
        asyncio.run(generator.close())
        if cognition is not None:
            asyncio.run(cognition.close())


def test_cognition_inherits_authoritative_conversation_model(monkeypatch, tmp_path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    supervisor = run_daemon(monkeypatch, [
        "sylphy-entity", "--port", "0",
        "--autonomy", "--sandbox-root", str(sandbox),
        "--conversation-model", "gemma-4-artemis-override.gguf",
    ], tmp_path)
    generator = supervisor.kwargs["conversation_generator"]
    cognition = supervisor.kwargs["cognition_provider"]
    try:
        assert cognition._model == generator._model == "gemma-4-artemis-override.gguf"
        assert supervisor.kwargs["cognition_model_name"] == generator._model
    finally:
        asyncio.run(generator.close())
        asyncio.run(cognition.close())


def test_legacy_ollama_is_not_selected_by_default(monkeypatch, tmp_path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    supervisor = run_daemon(monkeypatch, [
        "sylphy-entity", "--port", "0",
        "--autonomy", "--sandbox-root", str(sandbox),
    ], tmp_path)
    generator = supervisor.kwargs["conversation_generator"]
    cognition = supervisor.kwargs["cognition_provider"]
    try:
        assert "ollama" not in generator._base_url
        assert "ollama" not in cognition._base_url
    finally:
        asyncio.run(generator.close())
        asyncio.run(cognition.close())


def test_shared_slot_prevents_concurrent_overcommit(monkeypatch, tmp_path) -> None:
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    supervisor = run_daemon(monkeypatch, [
        "sylphy-entity", "--port", "0",
        "--autonomy", "--sandbox-root", str(sandbox),
    ], tmp_path)
    generator = supervisor.kwargs["conversation_generator"]
    cognition = supervisor.kwargs["cognition_provider"]

    state = {"active": 0, "peak": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["active"] += 1
        state["peak"] = max(state["peak"], state["active"])
        await asyncio.sleep(0.05)
        state["active"] -= 1
        body = json.loads(request.content)
        if "messages" in body and isinstance(body["messages"], list) and "choices" not in str(body)[:40]:
            # Conversation request
            return httpx.Response(200, json={"choices": [{"message": {"content": "hello"}}]})
        payload = {
            "summary": "", "summary_evidence_refs": [], "findings": [], "proposals": [],
        }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(payload)}}]})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gen_client = generator._client
    cog_client = cognition._client
    generator._client = mock_client
    cognition._client = mock_client
    generator._owns_client = False
    cognition._owns_client = False
    try:
        from sylphy_entity.contracts.cognition import CognitionOperation, CognitionRequest

        request = CognitionRequest(
            operation=CognitionOperation.SUMMARIZE,
            purpose="Bounded summary",
            context={"task": "x"},
            source_refs=("obs-1",),
            timeout_seconds=2.0,
        )

        async def both():
            await asyncio.gather(
                generator.generate(messages=(ConversationTurn(role="user", content="hi"),)),
                cognition.execute(request),
            )

        asyncio.run(both())
        assert state["peak"] == 1
    finally:
        asyncio.run(mock_client.aclose())
        asyncio.run(gen_client.aclose())
        asyncio.run(cog_client.aclose())
        asyncio.run(generator.close())
        asyncio.run(cognition.close())
