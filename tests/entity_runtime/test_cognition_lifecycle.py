"""Cognition terminal-outcome lifecycle tests (P3-2B).

These exercise the real EntitySupervisor with faked providers and no live model.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from sylphy_entity.contracts.cognition import (
    CognitionFinding,
    CognitionOutcome,
    CognitionPayload,
    CognitionStatus,
)
from sylphy_entity.persistence.database import EntityDatabase
from sylphy_entity.persistence.event_store import EventStore
from sylphy_entity.runtime.supervisor import EntitySupervisor


class CompletingCognition:
    def __init__(self) -> None:
        self.requests = []
        self.closed = False

    async def execute(self, request, *, cancel_event=None) -> CognitionOutcome:
        self.requests.append(request)
        return CognitionOutcome(
            request_id=request.request_id,
            status=CognitionStatus.COMPLETED,
            result=CognitionPayload(
                summary="A small file was observed.",
                summary_evidence_refs=request.source_refs,
                findings=(
                    CognitionFinding(
                        kind="metadata_observation",
                        statement="The observation contains metadata only.",
                        confidence=0.95,
                        evidence_refs=request.source_refs,
                    ),
                ),
            ),
            model="fake-local-model",
            elapsed_ms=4,
        )

    async def close(self) -> None:
        self.closed = True


class BlockingCognition:
    def __init__(self) -> None:
        self.requests = []
        self.closed = False

    async def execute(self, request, *, cancel_event=None) -> CognitionOutcome:
        self.requests.append(request)
        await asyncio.Event().wait()
        return CognitionOutcome(request_id=request.request_id, status=CognitionStatus.COMPLETED)

    async def close(self) -> None:
        self.closed = True


class OutOfProvenanceCognition:
    def __init__(self) -> None:
        self.requests = []

    async def execute(self, request, *, cancel_event=None) -> CognitionOutcome:
        self.requests.append(request)
        return CognitionOutcome(
            request_id=request.request_id,
            status=CognitionStatus.COMPLETED,
            result=CognitionPayload(
                summary="An unsupported claim.",
                summary_evidence_refs=("invented-ref",),
            ),
            model="fake-local-model",
            elapsed_ms=1,
        )

    async def close(self) -> None:
        return None


def make_supervisor(tmp_path: Path, provider):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir(exist_ok=True)
    (sandbox / "notes.txt").write_text("content is not sent", encoding="utf-8")
    return EntitySupervisor(
        lock_path=tmp_path / "runtime" / "entity.lock",
        port=0,
        tick_interval_seconds=0.03,
        sandbox_root=sandbox,
        autonomy_enabled=True,
        cognition_provider=provider,
        cognition_model_name="fake-local-model",
    )


def journal_events(tmp_path: Path):
    database = EntityDatabase(tmp_path / "runtime" / "entity_runtime.sqlite3")
    database.open()
    try:
        store = EventStore(database)
        return tuple(
            stored.event for stored in store.read_after(0, limit=1000)
        )
    finally:
        database.close()


async def wait_for(predicate, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met before deadline")


@pytest.mark.asyncio
async def test_cancellation_creates_terminal_cancelled_result_and_receipt(tmp_path) -> None:
    provider = BlockingCognition()
    supervisor = make_supervisor(tmp_path, provider)
    await supervisor.start()
    try:
        await wait_for(lambda: bool(provider.requests))
        assert supervisor._cognition_tasks
    finally:
        await supervisor.shutdown()

    events = journal_events(tmp_path)
    results = [
        event for event in events
        if event.event_type == "cognition.result"
        and event.payload.get("outcome", {}).get("request_id") == provider.requests[0].request_id
    ]
    assert len(results) == 1
    assert results[0].payload["outcome"]["status"] == "cancelled"
    assert results[0].payload["outcome"]["error_code"] == "cancelled"
    assert len(supervisor._brain_state.cognition_receipts) == 1
    receipt = supervisor._brain_state.cognition_receipts[0]
    assert receipt.request_id == provider.requests[0].request_id
    assert receipt.status == "cancelled"
    assert receipt.accepted_as_evidence is False


@pytest.mark.asyncio
async def test_inflight_registry_is_empty_after_cancelled_task(tmp_path) -> None:
    provider = BlockingCognition()
    supervisor = make_supervisor(tmp_path, provider)
    await supervisor.start()
    try:
        # Cancel after invocation, i.e. the window where a provider call has
        # happened but no terminal outcome exists yet.
        await wait_for(lambda: bool(provider.requests) and bool(supervisor._cognition_tasks))
        request_id = next(iter(supervisor._cognition_tasks))
        task = supervisor._cognition_tasks[request_id]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await wait_for(lambda: bool(supervisor._brain_state.cognition_receipts))
        assert supervisor._cognition_tasks == {}
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_exactly_one_terminal_result_and_one_receipt_for_completion(tmp_path) -> None:
    provider = CompletingCognition()
    supervisor = make_supervisor(tmp_path, provider)
    await supervisor.start()
    try:
        await wait_for(lambda: bool(supervisor._brain_state.cognition_receipts))
        await asyncio.sleep(0.15)  # let any extra ticks settle
        request_id = provider.requests[0].request_id
        events = tuple(supervisor._iter_journal_events())
        requested = [
            event for event in events
            if event.event_type == "cognition.requested"
            and event.payload.get("request_id") == request_id
        ]
        results = [
            event for event in events
            if event.event_type == "cognition.result"
            and event.payload.get("outcome", {}).get("request_id") == request_id
        ]
        assert len(provider.requests) == 1
        assert len(requested) == 1
        assert len(results) == 1
        assert len(supervisor._brain_state.cognition_receipts) == 1
        assert supervisor._cognition_tasks == {}
        assert await supervisor._resource_arbiter.snapshot() == ()
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_replay_deduplication_keeps_exactly_one_receipt(tmp_path) -> None:
    provider = CompletingCognition()
    supervisor = make_supervisor(tmp_path, provider)
    await supervisor.start()
    try:
        await wait_for(lambda: bool(supervisor._brain_state.cognition_receipts))
        before = len(supervisor._brain_state.cognition_receipts)
        supervisor._replay_unprocessed_cognition_results()
        supervisor._replay_unprocessed_cognition_results()
        assert len(supervisor._brain_state.cognition_receipts) == before == 1
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_restart_recovery_does_not_reinvoke_completed_request(tmp_path) -> None:
    provider = CompletingCognition()
    supervisor = make_supervisor(tmp_path, provider)
    await supervisor.start()
    first_request_ids = set()
    try:
        await wait_for(lambda: bool(supervisor._brain_state.cognition_receipts))
        first_request_ids = {request.request_id for request in provider.requests}
    finally:
        await supervisor.shutdown()

    recovered = CompletingCognition()
    second = make_supervisor(tmp_path, recovered)
    await second.start()
    try:
        await asyncio.sleep(0.15)
        # A fresh post-restart observation may legitimately create a new
        # request; the completed request itself must never be re-invoked.
        assert not ({request.request_id for request in recovered.requests} & first_request_ids)
        assert len(second._brain_state.cognition_receipts) >= 1
        assert second._cognition_tasks == {}
    finally:
        await second.shutdown()


@pytest.mark.asyncio
async def test_review_error_is_observable_and_replay_recovers(tmp_path, caplog, monkeypatch) -> None:
    provider = CompletingCognition()
    supervisor = make_supervisor(tmp_path, provider)
    original = supervisor._apply_cognition_result_event
    calls = {"n": 0}

    def fail_once(event):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated review failure")
        return original(event)

    monkeypatch.setattr(supervisor, "_apply_cognition_result_event", fail_once)
    with caplog.at_level(logging.ERROR):
        await supervisor.start()
        try:
            await wait_for(lambda: calls["n"] >= 1)
            await asyncio.sleep(0.1)
            assert supervisor._brain_state.cognition_receipts == ()
            assert "review failed" in caplog.text
            supervisor._apply_cognition_result_event = original
            supervisor._replay_unprocessed_cognition_results()
            assert len(supervisor._brain_state.cognition_receipts) == 1
        finally:
            await supervisor.shutdown()


@pytest.mark.asyncio
async def test_cognition_uses_model_scoped_exclusive_lease(tmp_path) -> None:
    provider = CompletingCognition()
    supervisor = make_supervisor(tmp_path, provider)
    observed: dict = {}
    original_acquire = supervisor._resource_arbiter.acquire

    async def recording_acquire(owner, requirements, **kwargs):
        if str(owner).startswith("cognition:"):
            observed["owner"] = owner
            observed["requirements"] = requirements
        return await original_acquire(owner, requirements, **kwargs)

    supervisor._resource_arbiter.acquire = recording_acquire
    await supervisor.start()
    try:
        await wait_for(lambda: bool(supervisor._brain_state.cognition_receipts))
        assert observed["owner"].startswith("cognition:")
        requirement = observed["requirements"][0]
        assert requirement.resource_key == "llm:local:fake-local-model"
        assert requirement.mode.value == "exclusive"
        assert await supervisor._resource_arbiter.snapshot() == ()
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_brain_rejects_citations_outside_request(tmp_path) -> None:
    provider = OutOfProvenanceCognition()
    supervisor = make_supervisor(tmp_path, provider)
    await supervisor.start()
    try:
        await wait_for(lambda: bool(supervisor._brain_state.cognition_receipts))
        receipt = supervisor._brain_state.cognition_receipts[-1]
        assert receipt.accepted_as_evidence is False
        assert "outside the request" in receipt.review_reason
        assert receipt.summary == ""
    finally:
        await supervisor.shutdown()
