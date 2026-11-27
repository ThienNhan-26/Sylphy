"""Startup-failure cleanup coverage for the Entity Runtime."""

from __future__ import annotations

import logging

import pytest

from sylphy_entity.runtime.supervisor import EntitySupervisor


class RecordingGenerator:
    """Conversation generator that records close() and can fail during cleanup."""

    def __init__(self, *, close_error: Exception | None = None) -> None:
        self.close_calls = 0
        self.close_error = close_error

    async def generate(self, *, messages, timeout_seconds: float = 90.0) -> str:
        return ""

    async def close(self) -> None:
        self.close_calls += 1
        if self.close_error is not None:
            raise self.close_error


class FailingCognition:
    """Cognition provider whose close() fails during startup cleanup."""

    def __init__(self) -> None:
        self.close_calls = 0

    async def execute(self, request, *, cancel_event=None):
        raise AssertionError("cognition must not execute during a failed startup")

    async def close(self) -> None:
        self.close_calls += 1
        raise RuntimeError("cognition cleanup failed")


@pytest.mark.asyncio
async def test_startup_failure_cognition_cleanup_error_does_not_mask_startup_error(
    tmp_path, monkeypatch, caplog
) -> None:
    provider = FailingCognition()
    supervisor = EntitySupervisor(
        lock_path=tmp_path / "entity.lock",
        port=0,
        cognition_provider=provider,
        cognition_model_name="failing-local-model",
    )

    def fail_start() -> None:
        raise RuntimeError("simulated startup failure")

    monkeypatch.setattr(supervisor, "_write_control_token", fail_start)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="simulated startup failure") as exc_info:
            await supervisor.start()

    # The original startup exception is still the observable failure.
    assert "cognition cleanup failed" not in str(exc_info.value)
    assert provider.close_calls == 1
    assert "Could not stop cognition capability after startup failure" in caplog.text
    # Cleanup continued past cognition: instance lock released and DB closed.
    assert supervisor._lock.acquired is False
    with pytest.raises(RuntimeError, match="not open"):
        _ = supervisor._database.connection


@pytest.mark.asyncio
async def test_startup_failure_closes_conversation_generator_without_masking_error(
    tmp_path, monkeypatch, caplog
) -> None:
    generator = RecordingGenerator(close_error=RuntimeError("cleanup failed"))
    supervisor = EntitySupervisor(
        lock_path=tmp_path / "entity.lock",
        port=0,
        conversation_generator=generator,
    )

    # ConversationRuntime is constructed in __init__; fail later in start().
    def fail_start() -> None:
        raise RuntimeError("simulated startup failure")

    monkeypatch.setattr(supervisor, "_write_control_token", fail_start)

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError, match="simulated startup failure"):
            await supervisor.start()

    # The generator owned by ConversationRuntime is closed even though startup
    # failed; the cleanup failure is logged and never replaces the startup error.
    assert generator.close_calls == 1
    assert "Could not stop Conversation Runtime after startup failure" in caplog.text
