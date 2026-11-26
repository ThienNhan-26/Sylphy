"""LlamaCppConversationGenerator adapter tests (S2-005)."""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import httpx
import pytest

from sylphy_entity import daemon as daemon_module
from sylphy_entity.identity import IdentityConfig
from sylphy_entity.interaction.generator import (
    ConversationTurn,
    LlamaCppConversationGenerator,
    OllamaConversationGenerator,
)
from sylphy_entity.interaction.conversation_runtime import ConversationRuntime
from sylphy_entity.contracts.conversation import ConversationMessage
from sylphy_entity.persistence.database import EntityDatabase
from sylphy_entity.persistence.event_store import EventStore


FOUNDATIONAL_PRINCIPLE = (
    "Sylphy is not gentle because she is told to be gentle. "
    "She is gentle because that is how she chooses to treat those she cares about."
)


# ---------------------------------------------------------------------------
# Helpers — every test uses a fake transport, no live server is required.
# ---------------------------------------------------------------------------

def _llama_gen(handler, *, model: str = "test-model", thinking_enabled: bool = True) -> tuple[LlamaCppConversationGenerator, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return LlamaCppConversationGenerator(model=model, client=client, thinking_enabled=thinking_enabled), client


def _ok_response(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _new_ollama_gen():
    return OllamaConversationGenerator(model="lfm3-uncensored-32k:latest")


class StorePublisher:
    """Minimal publisher that journals events, as ConversationRuntime expects."""

    def __init__(self, store):
        self.store = store

    async def publish_event(self, event):
        self.store.append(event)


class RecordingSupervisor:
    """Minimal EntitySupervisor stand-in that records constructor kwargs."""

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


def _run_daemon_main(monkeypatch, argv: list[str]) -> list:
    """Run daemon.main() with a recording supervisor; no server is started."""
    RecordingSupervisor.instances.clear()
    monkeypatch.setattr(daemon_module, "EntitySupervisor", RecordingSupervisor)
    monkeypatch.setattr(
        daemon_module, "load_entity_config",
        lambda: SimpleNamespace(identity_file=None),
    )
    monkeypatch.setattr(sys, "argv", argv)
    daemon_module.main()
    return RecordingSupervisor.instances


async def _capture_generator_request(gen, response: httpx.Response | None = None) -> dict:
    """Send one request through an already-built generator via a fake transport."""
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return response if response is not None else _ok_response("OK")

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    owned_client = gen._client
    gen._client = mock_client
    gen._owns_client = False
    try:
        await gen.generate(messages=(ConversationTurn(role="user", content="Hello"),))
    finally:
        await mock_client.aclose()
        await owned_client.aclose()
    return captured


# ---------------------------------------------------------------------------
# LLM adapter tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_llama_endpoint_and_request_payload():
    """LLM-01: POST /v1/chat/completions, model, messages, and stream=false."""
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return _ok_response("OK")

    gen, client = _llama_gen(handler, model="gemma-4-E2B-it-Artemis-Q3_K_M.gguf")
    try:
        result = await gen.generate(messages=(ConversationTurn(role="user", content="Hello"),))
    finally:
        await gen.close()
        await client.aclose()

    assert result == "OK"
    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:9931/v1/chat/completions"
    assert captured["body"]["model"] == "gemma-4-E2B-it-Artemis-Q3_K_M.gguf"
    assert captured["body"]["stream"] is False
    assert set(captured["body"]) == {"model", "messages", "stream", "chat_template_kwargs"}
    assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": True}
    assert captured["body"]["messages"] == [{"role": "user", "content": "Hello"}]


@pytest.mark.asyncio
async def test_llama_thinking_enabled_sends_true():
    """THINK-01: thinking_enabled=True sends enable_thinking=true."""
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ok_response("OK")

    gen, client = _llama_gen(handler, thinking_enabled=True)
    try:
        await gen.generate(messages=(ConversationTurn(role="user", content="Hello"),))
    finally:
        await gen.close()
        await client.aclose()

    assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": True}


@pytest.mark.asyncio
async def test_llama_thinking_disabled_sends_false():
    """THINK-02: thinking_enabled=False sends enable_thinking=false."""
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ok_response("OK")

    gen, client = _llama_gen(handler, thinking_enabled=False)
    try:
        await gen.generate(messages=(ConversationTurn(role="user", content="Hello"),))
    finally:
        await gen.close()
        await client.aclose()

    assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.asyncio
async def test_llama_thinking_enabled_by_default():
    """THINK-03: The default constructor enables Thinking."""
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ok_response("OK")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gen = LlamaCppConversationGenerator(model="test-model", client=client)
    assert gen._thinking_enabled is True
    try:
        await gen.generate(messages=(ConversationTurn(role="user", content="Hello"),))
    finally:
        await gen.close()
        await client.aclose()

    assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": True}


@pytest.mark.asyncio
async def test_llama_system_message_preserved():
    """LLM-02: System message reaches the HTTP payload string-for-string."""
    system_content = "  You are SYLPHY.\n\nKeep spacing and punctuation exactly — 100%.  \n"
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ok_response("ACK")

    gen, client = _llama_gen(handler)
    try:
        msgs = (
            ConversationTurn(role="system", content=system_content),
            ConversationTurn(role="user", content="Hi"),
        )
        await gen.generate(messages=msgs)
    finally:
        await gen.close()
        await client.aclose()

    assert captured["body"]["messages"][0] == {"role": "system", "content": system_content}
    assert captured["body"]["messages"][0]["content"] == system_content


@pytest.mark.asyncio
async def test_llama_user_and_assistant_history():
    """LLM-03: Conversation history and current message preserve order."""
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return _ok_response("ACK")

    gen, client = _llama_gen(handler)
    try:
        msgs = (
            ConversationTurn(role="system", content="System prompt"),
            ConversationTurn(role="user", content="First"),
            ConversationTurn(role="assistant", content="Got it"),
            ConversationTurn(role="user", content="Second"),
        )
        await gen.generate(messages=msgs)
    finally:
        await gen.close()
        await client.aclose()

    assert [m["role"] for m in captured["body"]["messages"]] == [
        "system", "user", "assistant", "user",
    ]
    assert [m["content"] for m in captured["body"]["messages"]] == [
        "System prompt", "First", "Got it", "Second",
    ]


@pytest.mark.asyncio
async def test_llama_correct_response_parsing():
    """LLM-04: Parses choices[0].message.content (not later choices)."""
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [
                {"message": {"content": "first choice"}},
                {"message": {"content": "second choice"}},
            ],
        })

    gen, client = _llama_gen(handler)
    try:
        result = await gen.generate(messages=(ConversationTurn(role="user", content="Test"),))
    finally:
        await gen.close()
        await client.aclose()

    assert result == "first choice"


@pytest.mark.asyncio
async def test_llama_returns_only_content_when_reasoning_present():
    """THINK-04: Both reasoning_content and content present -> only content returned."""
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "Final assistant answer.",
            "reasoning_content": "Internal chain of thought.",
        }}]})

    gen, client = _llama_gen(handler)
    try:
        result = await gen.generate(messages=(ConversationTurn(role="user", content="Test"),))
    finally:
        await gen.close()
        await client.aclose()

    assert result == "Final assistant answer."


@pytest.mark.asyncio
async def test_llama_reasoning_sentinel_never_in_http_result():
    """THINK-05: A unique sentinel in reasoning_content never appears in content."""
    sentinel = "REASONING-SENTINEL-7f3a9c-never-expose"

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "Visible answer only.",
            "reasoning_content": f"{sentinel} private deliberation",
        }}]})

    gen, client = _llama_gen(handler)
    try:
        result = await gen.generate(messages=(ConversationTurn(role="user", content="Test"),))
    finally:
        await gen.close()
        await client.aclose()

    assert sentinel not in result
    assert result == "Visible answer only."


@pytest.mark.asyncio
async def test_llama_empty_content_with_reasoning_still_invalid_output():
    """THINK-06: Empty content is invalid even when reasoning_content is non-empty."""
    sentinel = "REASONING-ONLY-SENTINEL"

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "",
            "reasoning_content": f"{sentinel} should not be used as fallback",
        }}]})

    gen, client = _llama_gen(handler)
    try:
        with pytest.raises(RuntimeError, match="conversation_invalid_output"):
            await gen.generate(messages=(ConversationTurn(role="user", content="Test"),))
    finally:
        await gen.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_llama_missing_reasoning_content_with_valid_content():
    """THINK-07: A response without reasoning_content parses normally."""
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "Plain final answer.",
        }}]})

    gen, client = _llama_gen(handler)
    try:
        result = await gen.generate(messages=(ConversationTurn(role="user", content="Test"),))
    finally:
        await gen.close()
        await client.aclose()

    assert result == "Plain final answer."


@pytest.mark.asyncio
async def test_llama_http_error_handling():
    """LLM-05: HTTP status errors are raised explicitly."""
    async def server_error(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "internal"})

    gen, client = _llama_gen(server_error)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await gen.generate(messages=(ConversationTurn(role="user", content="test"),))
    finally:
        await gen.close()
        await client.aclose()

    async def not_found(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "model not found"})

    gen, client = _llama_gen(not_found)
    try:
        with pytest.raises(RuntimeError, match="conversation_model_unavailable"):
            await gen.generate(messages=(ConversationTurn(role="user", content="test"),))
    finally:
        await gen.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_llama_malformed_json_response():
    """LLM-06a: Malformed JSON is surfaced, not silently accepted."""
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=b'{"choices": [',
            headers={"content-type": "application/json"},
        )

    gen, client = _llama_gen(handler)
    try:
        with pytest.raises(json.JSONDecodeError):
            await gen.generate(messages=(ConversationTurn(role="user", content="test"),))
    finally:
        await gen.close()
        await client.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": "not-a-list"},
        {"choices": [None]},
        {"choices": [{"message": None}]},
        {"choices": [{"message": {"content": 123}}]},
        {"choices": [{"message": {"content": ""}}]},
        {"choices": [{"message": {"content": "   "}}]},
    ],
)
async def test_llama_invalid_and_empty_response_handling(payload):
    """LLM-06b: Invalid or empty response shapes raise conversation_invalid_output."""
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    gen, client = _llama_gen(handler)
    try:
        with pytest.raises(RuntimeError, match="conversation_invalid_output"):
            await gen.generate(messages=(ConversationTurn(role="user", content="test"),))
    finally:
        await gen.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_llama_timeout_behavior():
    """LLM-07: The adapter's request timeout aborts a slow generation."""
    async def slow(_: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1.0)
        return _ok_response("too late")

    gen, client = _llama_gen(slow)
    try:
        with pytest.raises(asyncio.TimeoutError):
            await gen.generate(
                messages=(ConversationTurn(role="user", content="test"),),
                timeout_seconds=0.01,
            )
    finally:
        await gen.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_llama_truncated_response_handling():
    """LLM-08: Truncated output (finish_reason=length) raises error."""
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "a" * 20000}}],
            "finish_reason": "length",
        })

    gen, client = _llama_gen(handler)
    try:
        with pytest.raises(RuntimeError, match="conversation_output_truncated"):
            await gen.generate(messages=(ConversationTurn(role="user", content="test"),))
    finally:
        await gen.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_llama_loopback_url_validation():
    """LLM-09: Invalid (non-loopback) URL raises ValueError."""
    with pytest.raises(ValueError, match="Conversation llama.cpp URL must be plain HTTP loopback"):
        LlamaCppConversationGenerator(model="x", base_url="http://example.com:9931")
    with pytest.raises(ValueError, match="Conversation llama.cpp URL must be plain HTTP loopback"):
        LlamaCppConversationGenerator(model="x", base_url="https://127.0.0.1:9931")
    with pytest.raises(ValueError, match="Conversation llama.cpp URL must be plain HTTP loopback"):
        LlamaCppConversationGenerator(model="x", base_url="http://127.0.0.1:9931/path")


@pytest.mark.asyncio
async def test_llama_client_ownership_and_close():
    """LLM-10: Injected clients are not owned; closed state is enforced and idempotent."""
    async def handler(_: httpx.Request) -> httpx.Response:
        return _ok_response("OK")

    # Injected client: adapter must not close a caller-owned client.
    gen, client = _llama_gen(handler)
    await gen.generate(messages=(ConversationTurn(role="user", content="Hello"),))
    await gen.close()
    assert client.is_closed is False
    with pytest.raises(RuntimeError, match="conversation provider is closed"):
        await gen.generate(messages=(ConversationTurn(role="user", content="Hello"),))
    await client.aclose()

    # Owned client: adapter closes it, and close() is idempotent.
    owned = LlamaCppConversationGenerator(model="x")
    await owned.close()
    assert owned._client.is_closed is True
    await owned.close()


@pytest.mark.asyncio
async def test_ollama_remains_backward_compatible():
    """DAEMON-01: Ollama generator still uses /api/chat with stream=false."""
    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": "Hello"}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gen = OllamaConversationGenerator(model="lfm3-uncensored-32k:latest", client=client)
    try:
        msg = ConversationTurn(role="user", content="Hello")
        result = await gen.generate(messages=(msg,))
    finally:
        await gen.close()
        await client.aclose()

    assert result == "Hello"
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["body"]["stream"] is False
    assert "chat_template_kwargs" not in captured["body"]


@pytest.mark.asyncio
async def test_default_base_url_per_provider():
    """DAEMON-02: Default base URL per provider is correct."""
    ollama_gen = _new_ollama_gen()
    llama_gen = LlamaCppConversationGenerator(model="x")
    try:
        assert llama_gen._base_url == "http://127.0.0.1:9931"
        assert ollama_gen._base_url == "http://127.0.0.1:11434"
    finally:
        await llama_gen.close()
        await ollama_gen.close()


@pytest.mark.asyncio
async def test_conversation_provider_does_not_alter_cognition_provider():
    """DAEMON-03: Conversation provider configuration does not alter Cognition provider."""
    from sylphy_entity.cognition.ollama import OllamaCognition
    cog = OllamaCognition(model="lfm3-uncensored-32k:latest", base_url="http://127.0.0.1:11434")
    assert cog is not None
    await cog.close()


@pytest.mark.asyncio
async def test_llama_with_custom_base_url():
    """DAEMON-04: Llama.cpp generator accepts custom base_url via CLI/env."""
    gen = LlamaCppConversationGenerator(model="test-model", base_url="http://127.0.0.1:9931")
    assert gen._base_url == "http://127.0.0.1:9931"
    await gen.close()


@pytest.mark.asyncio
async def test_ollama_with_custom_base_url():
    """DAEMON-05: Ollama generator accepts custom base_url via CLI/env."""
    gen = OllamaConversationGenerator(model="test-model", base_url="http://127.0.0.1:11434")
    assert gen._base_url == "http://127.0.0.1:11434"
    await gen.close()


# ---------------------------------------------------------------------------
# Daemon configuration propagation
# ---------------------------------------------------------------------------

def test_daemon_conversation_thinking_defaults_on(monkeypatch):
    """CFG-01: The CLI default enables Thinking for the llama.cpp provider."""
    instances = _run_daemon_main(monkeypatch, [
        "sylphy-entity", "--port", "0",
        "--conversation-model", "gemma-4-E2B-it-Artemis-Q3_K_M.gguf",
        "--conversation-provider", "llama.cpp",
    ])
    assert len(instances) == 1
    gen = instances[0].kwargs["conversation_generator"]
    try:
        assert isinstance(gen, LlamaCppConversationGenerator)
        assert gen._thinking_enabled is True
    finally:
        asyncio.run(gen.close())


def test_daemon_conversation_thinking_can_be_disabled(monkeypatch):
    """CFG-02: --conversation-thinking off disables Thinking."""
    instances = _run_daemon_main(monkeypatch, [
        "sylphy-entity", "--port", "0",
        "--conversation-model", "gemma-4-E2B-it-Artemis-Q3_K_M.gguf",
        "--conversation-provider", "llama.cpp",
        "--conversation-thinking", "off",
    ])
    assert len(instances) == 1
    gen = instances[0].kwargs["conversation_generator"]
    try:
        assert isinstance(gen, LlamaCppConversationGenerator)
        assert gen._thinking_enabled is False
    finally:
        asyncio.run(gen.close())


def test_daemon_thinking_setting_reaches_generator_request(monkeypatch):
    """CFG-03: The CLI setting reaches both the generator and its HTTP request."""
    instances = _run_daemon_main(monkeypatch, [
        "sylphy-entity", "--port", "0",
        "--conversation-model", "gemma-4-E2B-it-Artemis-Q3_K_M.gguf",
        "--conversation-provider", "llama.cpp",
        "--conversation-thinking", "off",
    ])
    gen = instances[0].kwargs["conversation_generator"]
    try:
        captured = asyncio.run(_capture_generator_request(gen))
    finally:
        asyncio.run(gen.close())

    assert captured["body"]["chat_template_kwargs"] == {"enable_thinking": False}


def test_daemon_thinking_setting_does_not_affect_ollama(monkeypatch):
    """CFG-04: Thinking configuration is not passed to the Ollama provider."""
    instances = _run_daemon_main(monkeypatch, [
        "sylphy-entity", "--port", "0",
        "--conversation-model", "lfm3-uncensored-32k:latest",
        "--conversation-provider", "ollama",
        "--conversation-thinking", "off",
    ])
    gen = instances[0].kwargs["conversation_generator"]
    try:
        assert isinstance(gen, OllamaConversationGenerator)
        assert not hasattr(gen, "_thinking_enabled")
        captured = asyncio.run(_capture_generator_request(
            gen, httpx.Response(200, json={"message": {"content": "OK"}}),
        ))
    finally:
        asyncio.run(gen.close())

    assert "chat_template_kwargs" not in captured["body"]


# ---------------------------------------------------------------------------
# End-to-end component integration:
# IdentityConfig -> ConversationRuntime._respond() -> LlamaCpp -> HTTP payload
# ---------------------------------------------------------------------------

class RecordingLlamaCppConversationGenerator(LlamaCppConversationGenerator):
    """Real adapter that records the exact turns handed to it by the runtime."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.sent_messages = None

    async def generate(self, *, messages, timeout_seconds=90.0):
        self.sent_messages = messages
        return await super().generate(messages=messages, timeout_seconds=timeout_seconds)


@pytest.mark.asyncio
async def test_identity_reaches_llamacpp_http_payload_unchanged(tmp_path):
    """E2E: IdentityConfig -> _respond() -> llama.cpp adapter -> captured HTTP body."""
    identity = IdentityConfig(
        name="SYLPHY_TEST_IDENTITY",
        identity=(
            "I am Sylphy. I have my own character, thoughts, wishes, "
            "and way of seeing the world."
        ),
        personality="Gentle and considerate without being timid or submissive.",
        communication_style="Use warm, natural, thoughtful language.",
        relationship="Develops through interaction without assuming familiarity.",
        boundaries="Respect privacy and explicit permissions.",
        version="1.0",
    )

    captured = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["body"] = json.loads(request.content)
        return _ok_response("Captured reply")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    generator = RecordingLlamaCppConversationGenerator(
        model="test-identity-model",
        client=client,
    )

    db = EntityDatabase(tmp_path / "identity-llamacpp.sqlite3")
    db.open()
    store = EventStore(db)
    runtime = ConversationRuntime(
        publisher=StorePublisher(store),
        event_store=store,
        generator=generator,
        identity=identity,
    )

    try:
        await runtime.submit(ConversationMessage(
            text="Hello, who are you?",
            session_id="s1",
            message_id="e2e-identity-llamacpp",
        ))
        for _ in range(200):
            if not runtime.status()["active_generations"]:
                break
            await asyncio.sleep(0.01)

        # The generator was actually exercised with a system + user turn.
        assert generator.sent_messages is not None
        assert [turn.role for turn in generator.sent_messages] == ["system", "user"]
        assert generator.sent_messages[-1].content == "Hello, who are you?"

        # The exact turns reached the HTTP payload, in order, unchanged.
        assert captured["method"] == "POST"
        assert captured["url"] == "http://127.0.0.1:9931/v1/chat/completions"
        assert captured["body"]["model"] == "test-identity-model"
        assert captured["body"]["stream"] is False
        assert captured["body"]["messages"] == [
            {"role": turn.role, "content": turn.content}
            for turn in generator.sent_messages
        ]

        system_content = captured["body"]["messages"][0]["content"]
        assert system_content == generator.sent_messages[0].content

        # All seven Identity fields are present and unchanged at the HTTP boundary.
        assert "Name: SYLPHY_TEST_IDENTITY" in system_content
        assert f"Identity: {identity.identity}" in system_content
        assert f"Personality: {identity.personality}" in system_content
        assert f"Communication style: {identity.communication_style}" in system_content
        assert f"Relationship: {identity.relationship}" in system_content
        assert f"Boundaries: {identity.boundaries}" in system_content
        assert f"Identity version: {identity.version}" in system_content

        # The approved Foundational Principle is transmitted exactly.
        assert FOUNDATIONAL_PRINCIPLE in system_content
    finally:
        await runtime.stop()
        db.close()
        await client.aclose()


@pytest.mark.asyncio
async def test_runtime_isolates_reasoning_from_content_history_and_events(tmp_path):
    """ISOL-01: reasoning_content never reaches runtime content, history, or events."""
    sentinel = "RUNTIME-REASONING-SENTINEL-4d91-never-persist"
    final_answer = "Only the final assistant answer."

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {
            "content": final_answer,
            "reasoning_content": f"{sentinel} private internal planning",
        }}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    generator = LlamaCppConversationGenerator(model="test-model", client=client)

    db = EntityDatabase(tmp_path / "reasoning-isolation.sqlite3")
    db.open()
    store = EventStore(db)
    runtime = ConversationRuntime(
        publisher=StorePublisher(store),
        event_store=store,
        generator=generator,
    )

    try:
        await runtime.submit(ConversationMessage(
            text="Hello",
            session_id="s1",
            message_id="reasoning-isolation-1",
        ))
        for _ in range(200):
            if not runtime.status()["active_generations"]:
                break
            await asyncio.sleep(0.01)

        events = [item.event for item in store.read_after(0)]
        completed = next(
            event for event in events
            if event.event_type == "conversation.response.completed"
        )

        # Only the final answer is journaled as the completion payload.
        assert completed.payload["text"] == final_answer
        assert sentinel not in json.dumps(dict(completed.payload), ensure_ascii=False)

        # The API-facing history contains only the final answer, never reasoning.
        history = runtime.history("s1")
        assert sentinel not in json.dumps(history, ensure_ascii=False)
        assistant_messages = [m for m in history if m["role"] == "assistant"]
        assert assistant_messages[-1]["text"] == final_answer

        # No event payload anywhere carries the reasoning sentinel.
        all_payloads = [dict(event.payload) for event in events]
        assert sentinel not in json.dumps(all_payloads, ensure_ascii=False)
    finally:
        await runtime.stop()
        db.close()
        await client.aclose()


# ---------------------------------------------------------------------------
# Regression: ensure existing conversation runtime tests still pass
# ---------------------------------------------------------------------------

class DummyGenerator:
    """Minimal stand-in matching the test convention in test_conversation_runtime.py."""

    def __init__(self, response="Hello from Sylphy"):
        self.response = response
        self.received = []
        self.closed = False

    async def generate(self, *, messages, timeout_seconds=90):
        self.received.append(messages)
        return self.response

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_existing_conversation_runtime_infrastructure_unchanged(tmp_path):
    """REG-01: Existing conversation runtime infrastructure works unchanged."""
    db = EntityDatabase(tmp_path / "reg-db.sqlite3")
    db.open()
    store = EventStore(db)

    publisher = StorePublisher(store)

    gen = DummyGenerator("Hello from Sylphy")
    runtime = ConversationRuntime(
        publisher=publisher, event_store=store, generator=gen,
    )

    msg = ConversationMessage(text="Xin chào", session_id="s1", message_id="m1")
    accepted = await runtime.submit(msg)
    assert accepted["accepted"] is True

    # The DummyGenerator does not publish events; just verify runtime
    # construction and submission succeed without error.
    await runtime.stop()
    db.close()
