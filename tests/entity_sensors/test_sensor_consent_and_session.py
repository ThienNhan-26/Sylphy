from datetime import datetime, timedelta, timezone

import pytest

from sylphy_entity.contracts.observation import Observation
from sylphy_entity.contracts.sensor import SensorConsentGrant, SensorKind
from sylphy_entity.execution.resource_arbiter import ResourceArbiter, ResourceBusy
from sylphy_entity.sensors import ConsentDenied, SensorAdapterSession, SensorConsentLedger, SensorSessionState


def make_grant(*, sensor=SensorKind.CAMERA, device_id="cam0", scope="device:camera:cam0", purpose="local_perception", expires_at=None):
    return SensorConsentGrant(
        sensor=sensor,
        device_id=device_id,
        permission_scope=scope,
        purpose=purpose,
        granted_by="user",
        expires_at=expires_at,
    )


def make_observation(*, sensor="camera", device_id="cam0", scope="device:camera:cam0", age=0.1, payload=None, freshness="current"):
    return Observation(
        source_id=f"sensor:{sensor}:{device_id}",
        subject=f"sensor:{sensor}:{device_id}",
        kind=f"sensor.{sensor}.metadata_observed",
        payload=payload or {"simulated": True, "sequence": 1},
        observed_at=datetime.now(timezone.utc) - timedelta(seconds=age),
        permission_scope=scope,
        freshness=freshness,
    )


@pytest.mark.asyncio
async def test_sensor_is_denied_by_default_without_explicit_consent():
    ledger = SensorConsentLedger()
    session = SensorAdapterSession(sensor=SensorKind.CAMERA, device_id="cam0", purpose="local_perception", consent=ledger, arbiter=ResourceArbiter(), sink=_sink)
    with pytest.raises(ConsentDenied, match="no active consent"):
        await session.start()
    assert session.state is SensorSessionState.STOPPED


@pytest.mark.asyncio
async def test_explicit_consent_allows_session_and_publishes_fresh_scoped_metadata():
    ledger = SensorConsentLedger()
    ledger.grant(make_grant())
    received = []

    async def sink(item):
        received.append(item)

    session = SensorAdapterSession(sensor=SensorKind.CAMERA, device_id="cam0", purpose="local_perception", consent=ledger, arbiter=ResourceArbiter(), sink=sink)
    await session.start()
    await session.publish(make_observation())
    assert len(received) == 1
    assert received[0].permission_scope == "device:camera:cam0"
    await session.stop()
    assert session.state is SensorSessionState.STOPPED


@pytest.mark.asyncio
async def test_revocation_stops_session_and_releases_resource_lease():
    ledger = SensorConsentLedger()
    ledger.grant(make_grant())
    arbiter = ResourceArbiter()
    session = SensorAdapterSession(sensor=SensorKind.CAMERA, device_id="cam0", purpose="local_perception", consent=ledger, arbiter=arbiter, sink=_sink)
    await session.start()
    assert len(await arbiter.snapshot()) == 1
    assert ledger.revoke(SensorKind.CAMERA, "cam0")
    with pytest.raises(ConsentDenied):
        await session.publish(make_observation())
    assert session.state is SensorSessionState.STOPPED
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_camera_device_resource_is_exclusive_between_sessions():
    ledger = SensorConsentLedger()
    ledger.grant(make_grant())
    arbiter = ResourceArbiter()
    first = SensorAdapterSession(sensor=SensorKind.CAMERA, device_id="cam0", purpose="local_perception", consent=ledger, arbiter=arbiter, sink=_sink)
    second = SensorAdapterSession(sensor=SensorKind.CAMERA, device_id="cam0", purpose="local_perception", consent=ledger, arbiter=arbiter, sink=_sink)
    await first.start()
    with pytest.raises(ResourceBusy):
        await second.start()
    await first.stop()
    await second.start()
    await second.stop()


@pytest.mark.asyncio
async def test_expired_consent_is_denied():
    ledger = SensorConsentLedger()
    now = datetime.now(timezone.utc)
    ledger.grant(make_grant(expires_at=now + timedelta(milliseconds=1)))
    import asyncio
    await asyncio.sleep(0.01)
    session = SensorAdapterSession(sensor=SensorKind.CAMERA, device_id="cam0", purpose="local_perception", consent=ledger, arbiter=ResourceArbiter(), sink=_sink)
    with pytest.raises(ConsentDenied, match="expired"):
        await session.start()


@pytest.mark.asyncio
async def test_scope_mismatch_and_stale_observation_are_rejected():
    ledger = SensorConsentLedger()
    ledger.grant(make_grant())
    session = SensorAdapterSession(sensor=SensorKind.CAMERA, device_id="cam0", purpose="local_perception", consent=ledger, arbiter=ResourceArbiter(), sink=_sink, max_observation_age_seconds=1)
    await session.start()
    with pytest.raises(ConsentDenied, match="scope"):
        await session.publish(make_observation(scope="device:camera:other"))
    # A consent-scope violation fails closed and releases the device.
    assert session.state is SensorSessionState.STOPPED

    await session.start()
    with pytest.raises(ValueError, match="stale"):
        await session.publish(make_observation(age=5))
    await session.stop()


@pytest.mark.asyncio
async def test_raw_binary_media_is_rejected_before_event_publication():
    ledger = SensorConsentLedger()
    ledger.grant(make_grant())
    received = []

    async def sink(item):
        received.append(item)

    # Observation's shared JSON contract rejects raw binary before it can be
    # placed on the Entity event path or persisted.
    with pytest.raises(TypeError, match="unsupported JSON contract value: bytes"):
        make_observation(payload={"frame_bytes": b"secret"})
    assert received == []


@pytest.mark.asyncio
async def test_microphone_uses_distinct_resource_and_consent_scope():
    ledger = SensorConsentLedger()
    ledger.grant(make_grant(sensor=SensorKind.MICROPHONE, device_id="mic0", scope="device:microphone:mic0"))
    received = []

    async def sink(item):
        received.append(item)

    session = SensorAdapterSession(sensor=SensorKind.MICROPHONE, device_id="mic0", purpose="local_perception", consent=ledger, arbiter=ResourceArbiter(), sink=sink)
    await session.start()
    await session.publish(make_observation(sensor="microphone", device_id="mic0", scope="device:microphone:mic0", payload={"duration_ms": 200, "speech_activity": True}))
    assert len(received) == 1
    await session.stop()


async def _sink(_item):
    return None


@pytest.mark.asyncio
async def test_active_sensor_renews_its_resource_lease_until_stopped():
    import asyncio

    ledger = SensorConsentLedger()
    ledger.grant(make_grant())
    arbiter = ResourceArbiter()
    session = SensorAdapterSession(
        sensor=SensorKind.CAMERA, device_id="cam0", purpose="local_perception",
        consent=ledger, arbiter=arbiter, sink=_sink,
        lease_ttl_seconds=0.06, lease_renew_interval_seconds=0.015,
    )
    await session.start()
    await asyncio.sleep(0.11)
    leases = await arbiter.snapshot()
    assert len(leases) == 1
    assert leases[0].owner_id == "sensor-adapter:camera:cam0"
    await session.stop()
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_revoked_consent_is_detected_by_lease_heartbeat():
    import asyncio

    ledger = SensorConsentLedger()
    ledger.grant(make_grant())
    arbiter = ResourceArbiter()
    session = SensorAdapterSession(
        sensor=SensorKind.CAMERA, device_id="cam0", purpose="local_perception",
        consent=ledger, arbiter=arbiter, sink=_sink,
        lease_ttl_seconds=0.1, lease_renew_interval_seconds=0.01,
    )
    await session.start()
    ledger.revoke(SensorKind.CAMERA, "cam0")
    await asyncio.sleep(0.04)
    assert session.state is SensorSessionState.STOPPED
    assert await arbiter.snapshot() == ()
