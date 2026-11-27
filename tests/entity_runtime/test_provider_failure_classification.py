"""Deterministic T5/T5 provider-failure classification tests (P3-4.2)."""

from __future__ import annotations

import asyncio
import logging

import pytest

from sylphy_entity.contracts.cognition import (
    CognitionOperation,
    CognitionRequest,
    CognitionStatus,
)
from sylphy_entity.contracts.decision import CognitionRequestProposal
from sylphy_entity.contracts.execution import ResourceMode, ResourceRequirement
from sylphy_entity.runtime.supervisor import EntitySupervisor

REQUEST_ID = "cog-failure-case"
SOURCE_REF = "obs-failure-1"
MODEL = "fake-local-model"


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


class ExplodingProvider:
    def __init__(self) -> None:
        self.requests = []
        self.closed = False

    async def execute(self, request, *, cancel_event=None):
        self.requests.append(request)
        raise RuntimeError("provider exploded")

    async def close(self) -> None:
        self.closed = True


class UntouchedProvider:
    def __init__(self) -> None:
        self.requests = []
        self.closed = False

    async def execute(self, request, *, cancel_event=None):
        self.requests.append(request)
        raise AssertionError("provider must not be invoked when lease acquisition fails")

    async def close(self) -> None:
        self.closed = True


def _supervisor(tmp_path, provider) -> EntitySupervisor:
    return EntitySupervisor(
        lock_path=tmp_path / "entity.lock",
        port=0,
        tick_interval_seconds=0.02,
        autonomy_enabled=True,
        cognition_provider=provider,
        cognition_model_name=MODEL,
    )


async def _wait_for(predicate, timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("condition not met before deadline")


def _result_event(supervisor: EntitySupervisor):
    matches = [
        event for event in supervisor._iter_journal_events()
        if event.event_type == "cognition.result"
        and event.payload.get("outcome", {}).get("request_id") == REQUEST_ID
    ]
    assert len(matches) == 1
    return matches[0].payload["outcome"]


@pytest.mark.asyncio
async def test_t5_unexpected_provider_exception_is_classified_runtime_cognition_failure(
    tmp_path, monkeypatch, caplog
) -> None:
    provider = ExplodingProvider()
    supervisor = _supervisor(tmp_path, provider)
    await supervisor.start()
    try:
        request = _request()
        monkeypatch.setattr(
            supervisor, "_build_cognition_request", lambda proposal, snapshot: request
        )
        with caplog.at_level(logging.ERROR):
            await supervisor._submit_cognition_proposals([_proposal()], None)
            await _wait_for(lambda: bool(supervisor._brain_state.cognition_receipts))

        outcome = _result_event(supervisor)
        assert outcome["status"] == CognitionStatus.FAILED.value
        assert outcome["error_code"] == "runtime_cognition_failure"
        assert "RuntimeError: provider exploded" in outcome["error_message"]
        assert len(provider.requests) == 1

        receipt = supervisor._brain_state.cognition_receipts[0]
        assert receipt.request_id == REQUEST_ID
        assert receipt.status == "failed"
        assert receipt.accepted_as_evidence is False
        assert receipt.review_reason == "provider outcome: failed"
        assert receipt.error_code == "runtime_cognition_failure"
        assert "Cognition task failed for" in caplog.text
        assert REQUEST_ID not in supervisor._cognition_tasks
        assert await supervisor._resource_arbiter.snapshot() == ()
    finally:
        await supervisor.shutdown()


@pytest.mark.asyncio
async def test_t6_resource_busy_is_classified_without_provider_invocation(
    tmp_path, monkeypatch
) -> None:
    provider = UntouchedProvider()
    supervisor = _supervisor(tmp_path, provider)
    await supervisor.start()
    try:
        # Occupy the cognition resource before the task attempts acquisition.
        blocking = await supervisor._resource_arbiter.acquire(
            "external-owner",
            (ResourceRequirement(resource_key=f"llm:local:{MODEL}", mode=ResourceMode.EXCLUSIVE),),
            ttl_seconds=30.0,
        )
        request = _request()
        monkeypatch.setattr(
            supervisor, "_build_cognition_request", lambda proposal, snapshot: request
        )
        await supervisor._submit_cognition_proposals([_proposal()], None)
        await _wait_for(lambda: bool(supervisor._brain_state.cognition_receipts))

        outcome = _result_event(supervisor)
        assert outcome["status"] == CognitionStatus.FAILED.value
        assert outcome["error_code"] == "cognition_resource_busy"
        assert outcome["error_message"] == "Local model inference resource is currently owned"
        assert provider.requests == []

        receipt = supervisor._brain_state.cognition_receipts[0]
        assert receipt.status == "failed"
        assert receipt.accepted_as_evidence is False
        assert receipt.review_reason == "provider outcome: failed"
        assert receipt.error_code == "cognition_resource_busy"
        assert REQUEST_ID not in supervisor._cognition_tasks
        snapshot = await supervisor._resource_arbiter.snapshot()
        assert {lease.lease_id for lease in snapshot} == {lease.lease_id for lease in blocking}
        await supervisor._resource_arbiter.release(blocking)
        assert await supervisor._resource_arbiter.snapshot() == ()
    finally:
        await supervisor.shutdown()
