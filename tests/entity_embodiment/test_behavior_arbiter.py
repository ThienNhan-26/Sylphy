import pytest
from sylphy_entity.embodiment import (
    AvatarBehavior, AvatarEvent, AvatarEventKind, BehaviorArbiter,
    BehaviorClaim, BodyRegion,
)


def event(kind, *, eid=None, session="s1", sequence=1):
    return AvatarEvent(kind=kind, source="test", event_id=eid or f"{kind.value}-{sequence}",
                       session_id=session, sequence=sequence)


def test_idle_is_fallback_and_processing_claim_is_applied():
    arbiter=BehaviorArbiter()
    assert arbiter.snapshot().behavior is AvatarBehavior.IDLE
    snap=arbiter.handle_event(event(AvatarEventKind.COGNITION_STARTED))
    assert snap.behavior is AvatarBehavior.PROCESSING
    assert BodyRegion.BODY in snap.region_owners


def test_speaking_owns_mouth_and_preempts_processing_only_on_overlapping_regions():
    arbiter=BehaviorArbiter()
    arbiter.handle_event(event(AvatarEventKind.COGNITION_STARTED, session="c1"))
    snap=arbiter.handle_event(event(AvatarEventKind.VOICE_PLAYBACK_STARTED, session="v1", sequence=2))
    assert snap.behavior is AvatarBehavior.SPEAKING
    assert snap.region_owners[BodyRegion.MOUTH].behavior is AvatarBehavior.SPEAKING
    assert snap.region_owners[BodyRegion.BODY].behavior is AvatarBehavior.PROCESSING


def test_pause_has_highest_priority_and_resume_restores_active_behavior():
    arbiter=BehaviorArbiter()
    arbiter.handle_event(event(AvatarEventKind.VOICE_PLAYBACK_STARTED))
    paused=arbiter.handle_event(event(AvatarEventKind.ENTITY_PAUSED, sequence=2))
    assert paused.behavior is AvatarBehavior.PAUSED
    assert all(owner.behavior is AvatarBehavior.PAUSED for owner in paused.region_owners.values())
    resumed=arbiter.handle_event(event(AvatarEventKind.ENTITY_RESUMED, sequence=3))
    assert resumed.behavior is AvatarBehavior.SPEAKING


def test_shutdown_is_terminal_priority_and_clears_previous_claims():
    arbiter=BehaviorArbiter()
    arbiter.handle_event(event(AvatarEventKind.VOICE_PLAYBACK_STARTED))
    snap=arbiter.handle_event(event(AvatarEventKind.ENTITY_STOPPED, sequence=2))
    assert snap.behavior is AvatarBehavior.SHUTDOWN
    assert len(snap.claims) == 1


def test_duplicate_events_are_idempotent():
    arbiter=BehaviorArbiter()
    e=event(AvatarEventKind.VOICE_PLAYBACK_STARTED, eid="same")
    first=arbiter.handle_event(e)
    second=arbiter.handle_event(e)
    assert first == second
    assert len(second.claims) == 1


def test_region_claims_are_arbitrated_independently():
    arbiter=BehaviorArbiter()
    arbiter.claim(BehaviorClaim(AvatarBehavior.PROCESSING, "brain",
        (BodyRegion.BODY, BodyRegion.HEAD), sequence=1))
    arbiter.claim(BehaviorClaim(AvatarBehavior.SPEAKING, "voice",
        (BodyRegion.HEAD, BodyRegion.MOUTH), sequence=2))
    snap=arbiter.snapshot()
    assert snap.region_owners[BodyRegion.BODY].owner == "brain"
    assert snap.region_owners[BodyRegion.HEAD].owner == "voice"
    assert snap.region_owners[BodyRegion.MOUTH].owner == "voice"


def test_idle_cannot_be_used_as_an_override_claim():
    arbiter=BehaviorArbiter()
    with pytest.raises(ValueError):
        arbiter.claim(BehaviorClaim(AvatarBehavior.IDLE, "idle", (BodyRegion.BODY,)))


def test_interrupted_behavior_is_cleared_when_new_speech_starts():
    arbiter=BehaviorArbiter()
    arbiter.handle_event(event(AvatarEventKind.VOICE_PLAYBACK_STARTED, session="v1"))
    arbiter.handle_event(event(AvatarEventKind.VOICE_PLAYBACK_INTERRUPTED, session="v1", sequence=2))
    assert arbiter.snapshot().behavior is AvatarBehavior.INTERRUPTED
    snap=arbiter.handle_event(event(AvatarEventKind.VOICE_PLAYBACK_STARTED, session="v2", sequence=3))
    assert snap.behavior is AvatarBehavior.SPEAKING


def test_pause_preserves_underlying_speaking_claim_and_resume_restores_it():
    arbiter = BehaviorArbiter()
    arbiter.handle_event(event(AvatarEventKind.VOICE_PLAYBACK_STARTED, session="v1", sequence=1))
    paused = arbiter.handle_event(event(AvatarEventKind.ENTITY_PAUSED, sequence=2))
    assert paused.behavior is AvatarBehavior.PAUSED
    resumed = arbiter.handle_event(event(AvatarEventKind.ENTITY_RESUMED, sequence=3))
    assert resumed.behavior is AvatarBehavior.SPEAKING
    assert resumed.region_owners[BodyRegion.MOUTH].session_id == "v1"


def test_stale_voice_terminal_sequence_cannot_release_current_session_claim():
    arbiter = BehaviorArbiter()
    arbiter.handle_event(event(AvatarEventKind.VOICE_PLAYBACK_STARTED, session="v1", sequence=4))
    stale = event(AvatarEventKind.VOICE_PLAYBACK_COMPLETED, session="v1", sequence=3)
    snap = arbiter.handle_event(stale)
    assert snap.behavior is AvatarBehavior.SPEAKING
    assert snap.region_owners[BodyRegion.MOUTH].session_id == "v1"


def test_voice_progress_advances_sequence_guard():
    arbiter = BehaviorArbiter()
    arbiter.handle_event(event(AvatarEventKind.VOICE_PLAYBACK_STARTED, session="v1", sequence=1))
    arbiter.handle_event(event(AvatarEventKind.VOICE_PLAYBACK_PROGRESS, session="v1", sequence=5))
    stale = event(AvatarEventKind.VOICE_PLAYBACK_COMPLETED, session="v1", sequence=4)
    assert arbiter.handle_event(stale).behavior is AvatarBehavior.SPEAKING


def test_shutdown_is_terminal_and_ignores_late_events_and_claims():
    arbiter = BehaviorArbiter()
    arbiter.handle_event(event(AvatarEventKind.ENTITY_STOPPED, sequence=2))
    after = arbiter.handle_event(event(AvatarEventKind.VOICE_PLAYBACK_STARTED, session="late", sequence=3))
    assert after.behavior is AvatarBehavior.SHUTDOWN
    assert len(after.claims) == 1
    arbiter.claim(BehaviorClaim(AvatarBehavior.SPEAKING, "late", (BodyRegion.MOUTH,)))
    assert arbiter.snapshot().behavior is AvatarBehavior.SHUTDOWN
    assert len(arbiter.snapshot().claims) == 1


def test_cognition_start_releases_transient_conversation_reaction():
    arbiter = BehaviorArbiter()
    arbiter.handle_event(event(AvatarEventKind.CONVERSATION_MESSAGE_RECEIVED, session="c1", sequence=1))
    snap = arbiter.handle_event(event(AvatarEventKind.COGNITION_STARTED, session="c1", sequence=2))
    assert snap.behavior is AvatarBehavior.PROCESSING
    assert all(claim.behavior is not AvatarBehavior.REACTING for claim in snap.claims)
