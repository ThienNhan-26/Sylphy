import asyncio
import json

import pytest

from sylphy_entity.contracts.events import EntityEvent
from sylphy_entity.contracts.cognition import (
    CognitionOutcome,
    CognitionPayload,
    CognitionFinding,
    CognitionStatus,
)
from sylphy_entity.runtime.supervisor import EntitySupervisor


class FakeCognition:
    def __init__(self):
        self.requests = []
        self.closed = False

    async def execute(self, request, *, cancel_event=None):
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

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_brain_runtime_cognition_round_trip_is_journaled_and_reviewed(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "notes.txt").write_text("this content is not sent to cognition", encoding="utf-8")
    provider = FakeCognition()
    supervisor = EntitySupervisor(
        lock_path=tmp_path / "runtime" / "entity.lock",
        port=0,
        tick_interval_seconds=0.03,
        sandbox_root=sandbox,
        autonomy_enabled=True,
        cognition_provider=provider,
        cognition_model_name="fake-local-model",
    )
    await supervisor.start()
    try:
        with pytest.raises(PermissionError, match="owned by the Runtime"):
            await supervisor.publish_event(
                EntityEvent(
                    event_type="cognition.result", source="external",
                    payload={"forged": True},
                )
            )
        for _ in range(200):
            if supervisor._brain_state.cognition_receipts:
                break
            await asyncio.sleep(0.025)
        assert len(provider.requests) == 1
        request = provider.requests[0]
        assert request.operation.value == "summarize"
        assert request.source_refs
        encoded_context = json.dumps(request.context)
        assert "metadata_only" in encoded_context
        assert "this content is not sent to cognition" not in encoded_context

        receipt = supervisor._brain_state.cognition_receipts[-1]
        assert receipt.request_id == request.request_id
        assert receipt.accepted_as_evidence is True
        assert receipt.proposals == ()
        status_review = supervisor.status()["cognition"]["recent_reviews"][-1]
        assert status_review["request_id"] == request.request_id
        assert status_review["accepted_as_evidence"] is True
        assert "summary" not in status_review
        assert await supervisor._resource_arbiter.snapshot() == ()

        journal = tuple(supervisor._iter_journal_events())
        assert any(
            event.event_type == "cognition.requested"
            and event.payload.get("request_id") == request.request_id
            for event in journal
        )
        assert any(
            event.event_type == "cognition.result"
            and event.payload.get("outcome", {}).get("request_id") == request.request_id
            for event in journal
        )
    finally:
        await supervisor.shutdown()
    assert provider.closed is True
