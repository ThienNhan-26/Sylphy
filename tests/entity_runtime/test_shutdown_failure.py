"""Normal shutdown with a failing Cognition provider close() (P3-4.3)."""

from __future__ import annotations

import logging

import pytest

from sylphy_entity.contracts.cognition import (
    CognitionOperation,
    CognitionOutcome,
    CognitionPayload,
    CognitionRequest,
    CognitionStatus,
)
from sylphy_entity.runtime.supervisor import EntitySupervisor


class FailingCloseProvider:
    def __init__(self) -> None:
        self.close_calls = 0
        self.executed = False

    async def execute(self, request: CognitionRequest, *, cancel_event=None) -> CognitionOutcome:
        self.executed = True
        return CognitionOutcome(
            request_id=request.request_id,
            status=CognitionStatus.COMPLETED,
            result=CognitionPayload(
                summary="Metadata observed.", summary_evidence_refs=request.source_refs
            ),
            model="fake-local-model",
            elapsed_ms=1,
        )

    async def close(self) -> None:
        self.close_calls += 1
        raise RuntimeError("provider close failed")


def _request() -> CognitionRequest:
    return CognitionRequest(
        request_id="cog-shutdown-case",
        operation=CognitionOperation.SUMMARIZE,
        purpose="Bounded summary",
        context={"task": "x"},
        source_refs=("obs-shutdown-1",),
        timeout_seconds=2.0,
    )


@pytest.mark.asyncio
async def test_normal_shutdown_cognition_close_failure_is_observable_and_marks_unclean(
    tmp_path, monkeypatch, caplog
) -> None:
    provider = FailingCloseProvider()
    supervisor = EntitySupervisor(
        lock_path=tmp_path / "entity.lock",
        port=0,
        cognition_provider=provider,
        cognition_model_name="fake-local-model",
    )
    await supervisor.start()
    _ = _request()  # documents the contract used by the provider
    token_path = supervisor._token_path
    assert token_path.exists()

    clean_marker_calls = []
    real_mark = supervisor._runtime_store.mark_clean_shutdown

    def spy_mark() -> None:
        clean_marker_calls.append(1)
        real_mark()

    monkeypatch.setattr(supervisor._runtime_store, "mark_clean_shutdown", spy_mark)

    with caplog.at_level(logging.ERROR):
        await supervisor.shutdown()

    # Failure is classified and logged, not swallowed.
    assert provider.close_calls == 1
    assert "Cognition capability did not stop cleanly" in caplog.text
    assert "provider close failed" in caplog.text
    assert clean_marker_calls == []

    # shutdown_clean == False is reflected by the ERROR terminal lifecycle.
    assert supervisor.lifecycle.value == "error"
    assert supervisor._shutdown_complete.is_set()

    # Terminal cleanup still ran completely despite the provider failure.
    assert supervisor._lock.acquired is False
    assert not token_path.exists()
    with pytest.raises(RuntimeError, match="not open"):
        _ = supervisor._database.connection

    # A second shutdown is a no-op; it neither retries close nor changes state.
    with caplog.at_level(logging.ERROR):
        await supervisor.shutdown()
    assert provider.close_calls == 1
    assert supervisor.lifecycle.value == "error"
    assert clean_marker_calls == []

    # A repeated direct close still raises, but cannot change Runtime state.
    with pytest.raises(RuntimeError, match="provider close failed"):
        await provider.close()
    assert provider.close_calls == 2
    assert supervisor.lifecycle.value == "error"
