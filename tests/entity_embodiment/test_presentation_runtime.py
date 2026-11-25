import pytest
from sylphy_entity.embodiment import (
    AnimationAction, AnimationCommand, AvatarBehavior, AvatarEvent, AvatarEventKind,
    BehaviorArbiter, BehaviorAnimationMapper, BodyRegion, PresentationRuntime,
)


class FakeRenderer:
    def __init__(self): self.commands=[]
    async def apply(self, command): self.commands.append(command)


def make_snapshot(kind, *, session="s1", sequence=1):
    arbiter=BehaviorArbiter()
    return arbiter.handle_event(AvatarEvent(kind, "test", event_id=f"{kind.value}-{sequence}",
        session_id=session, sequence=sequence))


def test_idle_maps_to_small_looping_idle_set():
    frame=BehaviorAnimationMapper().map(BehaviorArbiter().snapshot())
    assert frame.behavior is AvatarBehavior.IDLE
    assert {c.region for c in frame.commands} == {BodyRegion.BODY, BodyRegion.HEAD, BodyRegion.EYES}
    assert all(c.action is AnimationAction.START and c.loop for c in frame.commands)


def test_speaking_maps_mouth_to_lipsync_semantic_clip():
    snap=make_snapshot(AvatarEventKind.VOICE_PLAYBACK_STARTED)
    frame=BehaviorAnimationMapper().map(snap)
    mouth=next(c for c in frame.commands if c.region is BodyRegion.MOUTH)
    assert mouth.clip == "speaking.lipsync"
    assert mouth.session_id == "s1"


def test_processing_and_speaking_keep_independent_region_ownership():
    arbiter=BehaviorArbiter()
    arbiter.handle_event(AvatarEvent(AvatarEventKind.COGNITION_STARTED,"test",event_id="c",session_id="c1"))
    snap=arbiter.handle_event(AvatarEvent(AvatarEventKind.VOICE_PLAYBACK_STARTED,"test",event_id="v",session_id="v1"))
    frame=BehaviorAnimationMapper().map(snap)
    by_region={c.region:c for c in frame.commands}
    assert by_region[BodyRegion.BODY].behavior is AvatarBehavior.PROCESSING
    assert by_region[BodyRegion.MOUTH].behavior is AvatarBehavior.SPEAKING


def test_interrupted_maps_mouth_to_closed_clip_and_is_non_looping():
    snap=make_snapshot(AvatarEventKind.VOICE_PLAYBACK_INTERRUPTED, sequence=2)
    frame=BehaviorAnimationMapper().map(snap)
    mouth=next(c for c in frame.commands if c.region is BodyRegion.MOUTH)
    assert mouth.clip == "mouth.closed"
    assert mouth.loop is False


def test_pause_claim_covers_all_body_regions():
    snap=make_snapshot(AvatarEventKind.ENTITY_PAUSED)
    frame=BehaviorAnimationMapper().map(snap)
    assert {c.region for c in frame.commands} == set(BodyRegion)
    assert all(c.behavior is AvatarBehavior.PAUSED for c in frame.commands)


@pytest.mark.asyncio
async def test_runtime_starts_idle_commands_once_and_does_not_duplicate_them():
    renderer=FakeRenderer(); runtime=PresentationRuntime(renderer)
    snap=BehaviorArbiter().snapshot()
    await runtime.render(snap); await runtime.render(snap)
    assert len(renderer.commands) == 3
    assert all(c.action is AnimationAction.START for c in renderer.commands)


@pytest.mark.asyncio
async def test_runtime_crossfades_region_when_owner_changes():
    renderer=FakeRenderer(); runtime=PresentationRuntime(renderer)
    arbiter=BehaviorArbiter()
    await runtime.render(arbiter.snapshot())
    # Switch to processing: body/eyes change; head changes too.
    snap=arbiter.handle_event(AvatarEvent(AvatarEventKind.COGNITION_STARTED,"test",event_id="c",session_id="c1"))
    await runtime.render(snap)
    stops=[c for c in renderer.commands if c.action is AnimationAction.STOP]
    starts=[c for c in renderer.commands if c.action is AnimationAction.START]
    assert len(stops) == 3
    assert len(starts) == 6
    assert runtime.behavior is AvatarBehavior.PROCESSING


@pytest.mark.asyncio
async def test_runtime_only_updates_changed_regions_and_keeps_unchanged_claims():
    renderer=FakeRenderer(); runtime=PresentationRuntime(renderer)
    arbiter=BehaviorArbiter()
    processing=arbiter.handle_event(AvatarEvent(AvatarEventKind.COGNITION_STARTED,"test",event_id="c",session_id="c1"))
    await runtime.render(processing)
    count=len(renderer.commands)
    await runtime.render(processing)
    assert len(renderer.commands) == count


@pytest.mark.asyncio
async def test_stop_all_releases_every_renderer_command():
    renderer=FakeRenderer(); runtime=PresentationRuntime(renderer)
    await runtime.render(BehaviorArbiter().snapshot())
    await runtime.stop_all()
    assert len(runtime.active_commands) == 0
    assert len([c for c in renderer.commands if c.action is AnimationAction.STOP]) == 3


def test_animation_command_validates_fades_and_weight():
    with pytest.raises(ValueError):
        AnimationCommand(AnimationAction.START,"x","owner",AvatarBehavior.IDLE,
            "idle",BodyRegion.BODY,10,fade_in_seconds=-1)
    with pytest.raises(ValueError):
        AnimationCommand(AnimationAction.START,"x","owner",AvatarBehavior.IDLE,
            "idle",BodyRegion.BODY,10,weight=2)
