import asyncio
from datetime import datetime, timezone

import pytest

from sylphy_entity.contracts.sensor import SensorConsentGrant, SensorKind
from sylphy_entity.execution.resource_arbiter import ResourceArbiter
from sylphy_entity.sensors.camera import (
    CameraAdapter,
    CapturedFrame,
    MetadataFrameProcessor,
)
from sylphy_entity.sensors.consent import ConsentDenied, SensorConsentLedger
from sylphy_entity.sensors.session import SensorAdapterSession, SensorSessionState


class Pixels:
    def __init__(self, value=b"fake-frame"):
        self.value = value

    def tobytes(self):
        return self.value


class FakeCamera:
    def __init__(self, *, fail_open=False):
        self.fail_open = fail_open
        self.opened = False
        self.closed = False
        self.sequence = 0

    async def open(self):
        if self.fail_open:
            raise RuntimeError("camera unavailable")
        self.opened = True

    async def read(self):
        await asyncio.sleep(0)
        self.sequence += 1
        return CapturedFrame(
            pixels=Pixels(),
            captured_at=datetime.now(timezone.utc),
            sequence=self.sequence,
            width=320,
            height=240,
        )

    async def close(self):
        self.closed = True
        self.opened = False


def make_session(*, granted=True, sink=None):
    consent = SensorConsentLedger()
    if granted:
        consent.grant(SensorConsentGrant(
            sensor=SensorKind.CAMERA,
            device_id="camera-0",
            permission_scope="local-preview",
            purpose="user-requested-preview",
            granted_by="user",
        ))
    arbiter = ResourceArbiter()
    received = [] if sink is None else sink

    async def publish(observation):
        received.append(observation)

    session = SensorAdapterSession(
        sensor=SensorKind.CAMERA,
        device_id="camera-0",
        purpose="user-requested-preview",
        consent=consent,
        arbiter=arbiter,
        sink=publish,
        max_observation_age_seconds=5,
        lease_ttl_seconds=3,
        lease_renew_interval_seconds=1,
    )
    return session, consent, arbiter, received


@pytest.mark.asyncio
async def test_camera_adapter_requires_explicit_consent_before_opening_device():
    session, _, arbiter, received = make_session(granted=False)
    backend = FakeCamera()
    adapter = CameraAdapter(
        device_id="camera-0",
        session=session,
        backend=backend,
        processor=MetadataFrameProcessor(),
        capture_interval_seconds=0.01,
    )

    with pytest.raises(ConsentDenied):
        await adapter.start()

    assert not backend.opened
    assert not received
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_camera_frames_are_processed_to_metadata_only_observations():
    session, _, arbiter, received = make_session()
    backend = FakeCamera()
    adapter = CameraAdapter(
        device_id="camera-0",
        session=session,
        backend=backend,
        processor=MetadataFrameProcessor(),
        capture_interval_seconds=0.005,
    )

    await adapter.start()
    for _ in range(100):
        if received:
            break
        await asyncio.sleep(0.002)
    await adapter.stop()

    assert received
    observation = received[0]
    assert observation.source_id == "sensor:camera:camera-0"
    assert observation.kind == "camera.frame.processed"
    assert observation.payload["analysis"] == "metadata_only"
    assert observation.payload["width"] == 320
    assert "frame_sha256" in observation.payload
    assert session.state is SensorSessionState.STOPPED
    assert backend.closed
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_camera_open_failure_releases_sensor_lease():
    session, _, arbiter, _ = make_session()
    backend = FakeCamera(fail_open=True)
    adapter = CameraAdapter(
        device_id="camera-0",
        session=session,
        backend=backend,
        processor=MetadataFrameProcessor(),
    )

    with pytest.raises(RuntimeError, match="camera unavailable"):
        await adapter.start()

    assert session.state is SensorSessionState.STOPPED
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_camera_frame_pixel_limit_fails_closed_and_releases_device():
    session, _, arbiter, _ = make_session()
    backend = FakeCamera()
    adapter = CameraAdapter(
        device_id="camera-0",
        session=session,
        backend=backend,
        processor=MetadataFrameProcessor(),
        capture_interval_seconds=0.005,
        max_frame_pixels=100,
    )

    await adapter.start()
    for _ in range(100):
        if not adapter.running:
            break
        await asyncio.sleep(0.002)

    assert adapter.last_error is not None
    assert "pixel limit" in adapter.last_error
    assert session.state is SensorSessionState.STOPPED
    assert backend.closed
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_raw_media_from_processor_is_rejected_before_observation_sink():
    class RawProcessor:
        async def process(self, frame):
            return {"raw": b"do-not-persist"}

    session, _, arbiter, received = make_session()
    backend = FakeCamera()
    adapter = CameraAdapter(
        device_id="camera-0",
        session=session,
        backend=backend,
        processor=RawProcessor(),
        capture_interval_seconds=0.005,
    )

    await adapter.start()
    for _ in range(100):
        if not adapter.running:
            break
        await asyncio.sleep(0.002)

    assert adapter.last_error is not None
    assert "raw binary media" in adapter.last_error
    assert received == []
    assert session.state is SensorSessionState.STOPPED
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_camera_adapter_rejects_wrong_sensor_session():
    session, _, _, _ = make_session()
    # The constructor checks sensor type before any backend is opened.
    from sylphy_entity.sensors.camera import CameraAdapter
    from sylphy_entity.contracts.sensor import SensorKind
    from sylphy_entity.sensors.session import SensorAdapterSession
    from sylphy_entity.execution.resource_arbiter import ResourceArbiter
    consent = SensorConsentLedger()
    wrong = SensorAdapterSession(
        sensor=SensorKind.MICROPHONE,
        device_id="camera-0",
        purpose="user-requested-preview",
        consent=consent,
        arbiter=ResourceArbiter(),
        sink=lambda observation: asyncio.sleep(0),
    )
    with pytest.raises(ValueError, match="CAMERA"):
        CameraAdapter(
            device_id="camera-0",
            session=wrong,
            backend=FakeCamera(),
            processor=MetadataFrameProcessor(),
        )
