from datetime import datetime, timezone

from sylphy_entity.brain import EntityBrain
from sylphy_entity.contracts.decision import BrainState, CognitionFeedback
from sylphy_entity.contracts.world import FactState, WorldFact, WorldSnapshot


def _world(state=FactState.KNOWN):
    fact = WorldFact(
        subject="file:notes.txt",
        state=state,
        value={
            "relative_path": "notes.txt", "kind": "filesystem.file_created",
            "size_bytes": 12, "modified_ns": 123456, "is_directory": False,
        },
        last_observation_id="obs-cog-1",
        last_observed_at=datetime.now(timezone.utc),
        source_id="filesystem:sandbox",
        confidence=0.95,
        verification="confirmed",
        permission_scope="sandbox:/allowed",
    )
    return WorldSnapshot(version=1, facts=(fact,))


def test_brain_proposes_cognition_only_when_runtime_capability_is_available():
    brain = EntityBrain()
    disabled = brain.step(1.0, _world(), BrainState())
    assert disabled.cognition_proposals == ()

    enabled = brain.step(1.0, _world(), BrainState(), cognition_available=True)
    assert len(enabled.cognition_proposals) == 1
    proposal = enabled.cognition_proposals[0]
    assert proposal.operation == "summarize"
    assert proposal.source_refs == ("obs-cog-1",)
    assert "metadata" in proposal.purpose
    assert "obs-cog-1" in enabled.next_state.cognition_requested_refs


def test_same_observation_is_not_requested_twice():
    brain = EntityBrain()
    first = brain.step(1.0, _world(), BrainState(), cognition_available=True)
    second = brain.step(1.0, _world(), first.next_state, cognition_available=True)
    assert second.cognition_proposals == ()


def test_brain_accepts_only_current_evidence_and_never_emits_intent_from_cognition():
    brain = EntityBrain()
    feedback = CognitionFeedback(
        request_id="cog-1",
        status="completed",
        source_refs=("obs-cog-1",),
        summary="A file was created.",
        summary_evidence_refs=("obs-cog-1",),
        findings=(("file_event", "The file is small.", 0.8, ("obs-cog-1",)),),
        proposals=(("review", "notes.txt", "Consider reviewing it later.", ("obs-cog-1",)),),
        elapsed_ms=25, model="test-model",
    )
    frame = brain.step(0.0, _world(), BrainState(), cognition_feedback=(feedback,), allow_cognition_requests=False, allow_goal_proposals=False)
    receipt = frame.next_state.cognition_receipts[-1]
    assert receipt.accepted_as_evidence is True
    assert receipt.review_reason.startswith("accepted as evidence-bounded")
    assert frame.intent_proposals == ()
    assert frame.goal_proposals == ()


def test_brain_rejects_stale_source_even_when_provider_says_completed():
    brain = EntityBrain()
    feedback = CognitionFeedback(
        request_id="cog-stale", status="completed",
        source_refs=("obs-cog-1",), summary="A file was created.",
        summary_evidence_refs=("obs-cog-1",),
    )
    frame = brain.step(0.0, _world(FactState.STALE), BrainState(), cognition_feedback=(feedback,), allow_cognition_requests=False, allow_goal_proposals=False)
    receipt = frame.next_state.cognition_receipts[-1]
    assert receipt.accepted_as_evidence is False
    assert "no longer current" in receipt.review_reason


def test_brain_rejects_reference_outside_request_provenance():
    brain = EntityBrain()
    feedback = CognitionFeedback(
        request_id="cog-invalid", status="completed",
        source_refs=("obs-cog-1",), summary="A file was created.",
        summary_evidence_refs=("invented-source",),
    )
    frame = brain.step(0.0, _world(), BrainState(), cognition_feedback=(feedback,), allow_cognition_requests=False, allow_goal_proposals=False)
    receipt = frame.next_state.cognition_receipts[-1]
    assert receipt.accepted_as_evidence is False
    assert "outside the request" in receipt.review_reason
