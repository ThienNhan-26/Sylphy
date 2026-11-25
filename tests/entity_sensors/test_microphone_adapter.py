import asyncio
from datetime import datetime, timezone

import pytest

from sylphy_entity.contracts.sensor import SensorConsentGrant, SensorKind
from sylphy_entity.execution.resource_arbiter import ResourceArbiter
from sylphy_entity.sensors.consent import ConsentDenied, SensorConsentLedger
from sylphy_entity.sensors.microphone import (
    AudioMetadataProcessor,
    CapturedAudio,
    MicrophoneAdapter,
)
from sylphy_entity.sensors.session import SensorAdapterSession, SensorSessionState


class FakeMicrophone:
    def __init__(self, *, fail_open=False):
        self.fail_open = fail_open
        self.opened = False
        self.closed = False
        self.sequence = 0

    async def open(self):
        if self.fail_open:
            raise RuntimeError("microphone unavailable")
        self.opened = True

    async def read(self):
        await asyncio.sleep(0.005)
        self.sequence += 1
        return CapturedAudio(
            samples=[0.0, 0.25, -0.25, 0.5],
            captured_at=datetime.now(timezone.utc),
            sequence=self.sequence,
            sample_rate=16000,
            channels=1,
            frames=4,
        )

    async def close(self):
        self.closed = True
        self.opened = False


def make_session(*, granted=True):
    consent = SensorConsentLedger()
    if granted:
        consent.grant(SensorConsentGrant(
            sensor=SensorKind.MICROPHONE,
            device_id="microphone-0",
            permission_scope="local-audio",
            purpose="user-requested-hearing",
            granted_by="user",
        ))
    arbiter = ResourceArbiter()
    received = []

    async def publish(observation):
        received.append(observation)

    session = SensorAdapterSession(
        sensor=SensorKind.MICROPHONE,
        device_id="microphone-0",
        purpose="user-requested-hearing",
        consent=consent,
        arbiter=arbiter,
        sink=publish,
        max_observation_age_seconds=5,
        lease_ttl_seconds=3,
        lease_renew_interval_seconds=1,
    )
    return session, consent, arbiter, received


@pytest.mark.asyncio
async def test_microphone_requires_explicit_consent_before_opening_device():
    session, _, arbiter, received = make_session(granted=False)
    backend = FakeMicrophone()
    adapter = MicrophoneAdapter(
        device_id="microphone-0",
        session=session,
        backend=backend,
        processor=AudioMetadataProcessor(),
    )
    with pytest.raises(ConsentDenied):
        await adapter.start()
    assert not backend.opened
    assert received == []
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_microphone_capture_emits_metadata_observation_and_stops_cleanly():
    session, _, arbiter, received = make_session()
    backend = FakeMicrophone()
    adapter = MicrophoneAdapter(
        device_id="microphone-0",
        session=session,
        backend=backend,
        processor=AudioMetadataProcessor(),
    )
    await adapter.start()
    for _ in range(100):
        if received:
            break
        await asyncio.sleep(0.002)
    await adapter.stop()

    assert received
    item = received[0]
    assert item.source_id == "sensor:microphone:microphone-0"
    assert item.kind == "microphone.audio.processed"
    assert item.payload["analysis"] == "audio_metadata_only"
    assert item.payload["sample_rate"] == 16000
    assert 0 <= item.payload["rms_level"] <= 1
    assert session.state is SensorSessionState.STOPPED
    assert backend.closed
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_microphone_open_failure_releases_device_lease():
    session, _, arbiter, _ = make_session()
    backend = FakeMicrophone(fail_open=True)
    adapter = MicrophoneAdapter(
        device_id="microphone-0",
        session=session,
        backend=backend,
        processor=AudioMetadataProcessor(),
    )
    with pytest.raises(RuntimeError, match="microphone unavailable"):
        await adapter.start()
    assert session.state is SensorSessionState.STOPPED
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_microphone_rejects_raw_audio_bytes_before_observation_sink():
    class RawProcessor:
        async def process(self, audio):
            return {"raw_audio": b"do-not-persist"}

    session, _, arbiter, received = make_session()
    backend = FakeMicrophone()
    adapter = MicrophoneAdapter(
        device_id="microphone-0",
        session=session,
        backend=backend,
        processor=RawProcessor(),
    )
    await adapter.start()
    for _ in range(100):
        if not adapter.running:
            break
        await asyncio.sleep(0.002)
    assert adapter.last_error is not None
    assert "raw audio media" in adapter.last_error
    assert received == []
    assert session.state is SensorSessionState.STOPPED
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_microphone_chunk_duration_limit_fails_closed():
    class LongChunkBackend(FakeMicrophone):
        async def read(self):
            await asyncio.sleep(0.001)
            return CapturedAudio(
                samples=[0.1],
                captured_at=datetime.now(timezone.utc),
                sequence=1,
                sample_rate=100,
                channels=1,
                frames=1000,
            )

    session, _, arbiter, received = make_session()
    backend = LongChunkBackend()
    adapter = MicrophoneAdapter(
        device_id="microphone-0",
        session=session,
        backend=backend,
        processor=AudioMetadataProcessor(),
        max_audio_seconds=2,
    )
    await adapter.start()
    for _ in range(100):
        if not adapter.running:
            break
        await asyncio.sleep(0.002)
    assert adapter.last_error is not None
    assert "duration limit" in adapter.last_error
    assert received == []
    assert backend.closed
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_microphone_rejects_camera_session():
    from sylphy_entity.sensors.camera import CameraAdapter, MetadataFrameProcessor
    from sylphy_entity.sensors.session import SensorAdapterSession
    consent = SensorConsentLedger()
    wrong = SensorAdapterSession(
        sensor=SensorKind.CAMERA,
        device_id="microphone-0",
        purpose="user-requested-hearing",
        consent=consent,
        arbiter=ResourceArbiter(),
        sink=lambda observation: asyncio.sleep(0),
    )
    with pytest.raises(ValueError, match="MICROPHONE"):
        MicrophoneAdapter(
            device_id="microphone-0",
            session=wrong,
            backend=FakeMicrophone(),
            processor=AudioMetadataProcessor(),
        )
