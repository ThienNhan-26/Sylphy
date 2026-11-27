"""Deterministic C1/C3 cancellation-window characterization tests (P3-4.1).

These tests document the *actual* behaviour of the current implementation.
They do not assert an ideal invariant that the code does not yet provide.
"""

from __future__ import annotations

import asyncio

import pytest

from sylphy_entity.contracts.cognition import (
    CognitionOperation,
    CognitionOutcome,
    CognitionPayload,
    CognitionRequest,
    CognitionStatus,
)
from sylphy_entity.contracts.decision import CognitionRequestProposal
from sylphy_entity.runtime.supervisor import EntitySupervisor

REQUEST_ID = "cog-edge-case"
SOURCE_REF = "obs-edge-1"


def _request() -> CognitionRequest:
    return CognitionRequest(
        request_id=REQUEST_ID,
        operation=CognitionOperation.SUMMARIZE,
        purpose="Bounded summary",
        context={"task": "x"},
        source_refs=(SOURCE_REF,),
        timeout_seconds=2.0,
    )


def _proposal() -> CognitionRequestProposal:
    return CognitionRequestProposal(
        request_id=REQUEST_ID,
        operation="summarize",
        purpose="Bounded summary",
        source_refs=(SOURCE_REF,),
    )


class FakeProvider:
    def __init__(self) -> None:
        self.requests: list[CognitionRequest] = []
        self.closed = False

    async def execute(self, request, *, cancel_event=None) -> CognitionOutcome:
        self.requests.append(request)
        return CognitionOutcome(
            request_id=request.request_id,
            status=CognitionStatus.COMPLETED,
            result=CognitionPayload(
                summary="Metadata observed.",
                summary_evidence_refs=request.source_refs,
            ),
            model="fake-local-model",
            elapsed_ms=1,
        )

    async def close(self) -> None:
        self.closed = True


def _supervisor(tmp_path, provider: FakeProvider) -> EntitySupervisor:
    return EntitySupervisor(
        lock_path=tmp_path / "entity.lock",
        port=0,
        tick_interval_seconds=0.02,
        autonomy_enabled=True,
        cognition_provider=provider,
        cognition_model_name="fake-local-model",
    )


def _journal(supervisor: EntitySupervisor, event_type: str) -> list:
    return [
        event for event in supervisor._iter_journal_events()
        if event.event_type == event_type
        and event.payload.get("request_id") == REQUEST_ID
    ]


@pytest.mark.asyncio
async def test_c1_cancel_before_coroutine_start_journals_request_without_terminal_result(
    tmp_path, monkeypatch
) -> None:
    provider = FakeProvider()
    supervisor = _supervisor(tmp_path, provider)
    await supervisor.start()
    try:
        request = _request()
        monkeypatch.setattr(
            supervisor, "_build_cognition_request", lambda proposal, snapshot: request
        )
        real_create_task = asyncio.create_task
        created = []

        def intercept(coro, **kwargs):
            task = real_create_task(coro, **kwargs)
            created.append(task)
            task.cancel()  # cancelled before the coroutine body ever executes
            return task

        with monkeypatch.context() as patch:
            patch.setattr(asyncio, "create_task", intercept)
            await supervisor._submit_cognition_proposals([_proposal()], None)
        await asyncio.sleep(0)
        await asyncio.gather(*created, return_exceptions=True)

        # Transient in-process state: request durable, no terminal result,
        # no receipt, stale registry entry, no lease held.
        assert provider.requests == []
        assert len(_journal(supervisor, "cognition.requested")) == 1
        result_events = [
            event for event in supervisor._iter_journal_events()
            if event.event_type == "cognition.result"
            and event.payload.get("outcome", {}).get("request_id") == REQUEST_ID
        ]
        assert result_events == []
        assert supervisor._brain_state.cognition_receipts == ()
        assert REQUEST_ID in supervisor._cognition_tasks
        assert await supervisor._resource_arbiter.snapshot() == ()
    finally:
        await supervisor.shutdown()

    # Recovery: startup replay converts the unmatched request into a terminal
    # stale-source result and a receipt; no provider invocation is repeated.
    recovered_provider = FakeProvider()
    second = _supervisor(tmp_path, recovered_provider)
    await second.start()
    try:
        receipts = [
            receipt for receipt in second._brain_state.cognition_receipts
            if receipt.request_id == REQUEST_ID
        ]
        assert len(receipts) == 1
        assert receipts[0].error_code == "stale_source_after_restart"
        assert recovered_provider.requests == []
    finally:
        await second.shutdown()


@pytest.mark.asyncio
async def test_c3_cancel_during_result_publication_leaves_durable_result_without_receipt(
    tmp_path, monkeypatch
) -> None:
    provider = FakeProvider()
    supervisor = _supervisor(tmp_path, provider)
    await supervisor.start()
    try:
        request = _request()
        monkeypatch.setattr(
            supervisor, "_build_cognition_request", lambda proposal, snapshot: request
        )
        real_create_task = asyncio.create_task
        created = []

        def intercept(coro, **kwargs):
            task = real_create_task(coro, **kwargs)
            created.append(task)
            return task

        async def cancelled_publish(event) -> None:
            # Append has already committed; cancellation arrives at the queue await.
            raise asyncio.CancelledError()

        monkeypatch.setattr(supervisor._event_loop, "publish", cancelled_publish)

        with monkeypatch.context() as patch:
            patch.setattr(asyncio, "create_task", intercept)
            await supervisor._submit_cognition_proposals([_proposal()], None)
        await asyncio.sleep(0)
        await asyncio.gather(*created, return_exceptions=True)

        result_events = [
            event for event in supervisor._iter_journal_events()
            if event.event_type == "cognition.result"
            and event.payload.get("outcome", {}).get("request_id") == REQUEST_ID
        ]
        assert len(provider.requests) == 1
        assert len(_journal(supervisor, "cognition.requested")) == 1
        assert len(result_events) == 1
        assert result_events[0].payload["outcome"]["status"] == "completed"
        # Missing receipt is transient: replay recreates it deterministically.
        assert supervisor._brain_state.cognition_receipts == ()
        assert REQUEST_ID not in supervisor._cognition_tasks
        assert await supervisor._resource_arbiter.snapshot() == ()
        supervisor._replay_unprocessed_cognition_results()
        assert len(supervisor._brain_state.cognition_receipts) == 1
    finally:
        await supervisor.shutdown()

    recovered_provider = FakeProvider()
    second = _supervisor(tmp_path, recovered_provider)
    await second.start()
    try:
        receipts = [
            receipt for receipt in second._brain_state.cognition_receipts
            if receipt.request_id == REQUEST_ID
        ]
        assert len(receipts) == 1
        assert recovered_provider.requests == []
    finally:
        await second.shutdown()
