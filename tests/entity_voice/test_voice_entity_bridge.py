import asyncio
import numpy as np
import pytest
from sylphy_entity.voice import VoiceCapability, VoiceRuntime, VoiceSession
from sylphy_entity.voice.contracts import VoiceAudio, VoiceEventType


class FakeTTS:
    async def synthesize(self, session):
        return VoiceAudio(np.zeros(160, dtype=np.float32), 16000, 0.01)


class FakeOutput:
    async def play(self, audio, *, on_started=None, progress=None):
        if on_started:
            await on_started()
        if progress:
            await progress(audio.duration_seconds, audio.duration_seconds, 0.4)
    async def stop(self):
        return None


class FakeEntity:
    def __init__(self): self.events=[]
    async def publish_event(self, event): self.events.append(event)


@pytest.mark.asyncio
async def test_voice_lifecycle_is_forwarded_to_existing_entity_publisher():
    entity=FakeEntity()
    capability=VoiceCapability(VoiceRuntime(tts=FakeTTS(), output=FakeOutput()), entity)
    await capability.speak(VoiceSession("hello"))
    names=[event.event_type for event in entity.events]
    assert names == [
        "voice.session.created", "voice.synthesis.started", "voice.synthesis.completed",
        "voice.playback.started", "voice.playback.progress", "voice.playback.completed",
    ]
    assert all(event.source == "voice-output" for event in entity.events)
    assert [e.payload["sequence"] for e in entity.events] == list(range(1,7))
    assert capability.status()["state"] == "completed"
    assert capability.status()["is_speaking"] is False
    assert capability.status()["session_generation"] == 1
    progress = next(event for event in entity.events if event.event_type == "voice.playback.progress")
    assert progress.payload["amplitude"] == pytest.approx(0.4)
    assert progress.payload["position_seconds"] == pytest.approx(0.01)


@pytest.mark.asyncio
async def test_voice_capability_does_not_require_entity_specific_bus():
    entity=FakeEntity()
    capability=VoiceCapability(VoiceRuntime(tts=FakeTTS(), output=FakeOutput()), entity)
    await capability.speak(VoiceSession("hello"))
    assert len(entity.events) == 6

@pytest.mark.asyncio
async def test_voice_status_snapshot_exposes_clock_and_generation():
    entity = FakeEntity()
    capability = VoiceCapability(VoiceRuntime(tts=FakeTTS(), output=FakeOutput()), entity)
    snapshot = capability.status()
    assert snapshot["enabled"] is True
    assert snapshot["is_speaking"] is False
    assert snapshot["session_generation"] == 0
    assert snapshot["position_seconds"] == 0.0
    assert snapshot["amplitude"] == 0.0

@pytest.mark.asyncio
async def test_voice_status_api_is_read_only_and_includes_lipsync_clock():
    from types import SimpleNamespace
    from httpx import ASGITransport, AsyncClient
    from sylphy_entity.interface.local_api import create_app

    voice_snapshot = {
        "enabled": True,
        "state": "playing",
        "is_speaking": True,
        "active_session_id": "session-api",
        "entity_instance_id": "entity-api-instance",
        "session_generation": 3,
        "sequence": 9,
        "position_seconds": 0.45,
        "duration_seconds": 2.0,
        "amplitude": 0.32,
    }
    runtime = SimpleNamespace(
        status=lambda: {"lifecycle": "running"},
        voice_status=lambda: voice_snapshot,
    )
    app = create_app(runtime, control_token="not-needed-for-read-only-status")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/voice/status")
    assert response.status_code == 200
    assert response.json() == voice_snapshot


def test_supervisor_voice_status_scopes_generation_to_entity_instance():
    from types import SimpleNamespace
    from sylphy_entity.runtime.supervisor import EntitySupervisor

    supervisor = EntitySupervisor.__new__(EntitySupervisor)
    supervisor._voice_capability = None
    supervisor._boot_info = SimpleNamespace(instance_id="entity-instance-r6")
    status = supervisor.voice_status()
    assert status["enabled"] is False
    assert status["entity_instance_id"] == "entity-instance-r6"
    assert status["session_generation"] == 0


@pytest.mark.asyncio
async def test_avatar_presentation_api_is_read_only_and_returns_entity_frame():
    from types import SimpleNamespace
    from httpx import ASGITransport, AsyncClient
    from sylphy_entity.interface.local_api import create_app

    frame = {"revision": 4, "behavior": "speaking", "commands": [{"action": "start", "command_id": "voice:s1:mouth", "region": "mouth"}]}
    runtime = SimpleNamespace(
        status=lambda: {"lifecycle": "running"},
        avatar_presentation_status=lambda: frame,
    )
    app = create_app(runtime, control_token="read-only")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/avatar/presentation")
    assert response.status_code == 200
    assert response.json() == frame
