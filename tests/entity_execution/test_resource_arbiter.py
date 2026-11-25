import asyncio

import pytest

from sylphy_entity.contracts.execution import ResourceMode, ResourceRequirement
from sylphy_entity.execution.resource_arbiter import ResourceArbiter, ResourceBusy


@pytest.mark.asyncio
async def test_two_readers_can_share_the_same_path():
    arbiter = ResourceArbiter()
    requirement = (ResourceRequirement("filesystem:sandbox:/root/notes.txt", ResourceMode.SHARED),)
    one = await arbiter.acquire("intent-one", requirement)
    two = await arbiter.acquire("intent-two", requirement)
    assert len(await arbiter.snapshot()) == 2
    await arbiter.release(one)
    await arbiter.release(two)
    assert await arbiter.snapshot() == ()


@pytest.mark.asyncio
async def test_exclusive_claim_conflicts_with_existing_reader():
    arbiter = ResourceArbiter()
    reader = await arbiter.acquire("reader", (ResourceRequirement("camera", ResourceMode.SHARED),))
    with pytest.raises(ResourceBusy):
        await arbiter.acquire("writer", (ResourceRequirement("camera", ResourceMode.EXCLUSIVE),))
    await arbiter.release(reader)


@pytest.mark.asyncio
async def test_different_resources_are_independent_not_globally_busy():
    arbiter = ResourceArbiter()
    one = await arbiter.acquire("intent-one", (ResourceRequirement("filesystem:/a"),))
    two = await arbiter.acquire("intent-two", (ResourceRequirement("inference:slot-0"),))
    assert len(await arbiter.snapshot()) == 2
    await arbiter.release(one + two)


@pytest.mark.asyncio
async def test_multiresource_acquire_is_atomic_on_conflict():
    arbiter = ResourceArbiter()
    existing = await arbiter.acquire("existing", (ResourceRequirement("camera", ResourceMode.EXCLUSIVE),))
    with pytest.raises(ResourceBusy):
        await arbiter.acquire(
            "candidate",
            (
                ResourceRequirement("filesystem:/sandbox/a"),
                ResourceRequirement("camera", ResourceMode.SHARED),
            ),
        )
    assert {lease.owner_id for lease in await arbiter.snapshot()} == {"existing"}
    await arbiter.release(existing)


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimed():
    now = [10.0]
    arbiter = ResourceArbiter(clock=lambda: now[0])
    await arbiter.acquire("owner-a", (ResourceRequirement("resource"),), ttl_seconds=2.0)
    now[0] = 12.1
    lease = await arbiter.acquire("owner-b", (ResourceRequirement("resource", ResourceMode.EXCLUSIVE),))
    assert lease[0].owner_id == "owner-b"


@pytest.mark.asyncio
async def test_same_owner_cannot_bypass_an_exclusive_resource_claim():
    arbiter = ResourceArbiter()
    shared = (ResourceRequirement("filesystem:/sandbox/notes.txt", ResourceMode.SHARED),)
    exclusive = (ResourceRequirement("filesystem:/sandbox/notes.txt", ResourceMode.EXCLUSIVE),)
    held = await arbiter.acquire("reused-owner-id", shared)
    with pytest.raises(ResourceBusy):
        await arbiter.acquire("reused-owner-id", exclusive)
    await arbiter.release(held)


@pytest.mark.asyncio
async def test_resource_lease_can_be_renewed_without_changing_ownership():
    arbiter = ResourceArbiter()
    leases = await arbiter.acquire(
        "sensor:camera:cam0",
        (ResourceRequirement("sensor:camera:cam0", ResourceMode.EXCLUSIVE),),
        ttl_seconds=1.0,
    )
    renewed = await arbiter.renew(leases, ttl_seconds=5.0)
    assert renewed[0].lease_id == leases[0].lease_id
    assert renewed[0].owner_id == leases[0].owner_id
    assert renewed[0].expires_at_monotonic > leases[0].expires_at_monotonic
    assert await arbiter.snapshot() == renewed


@pytest.mark.asyncio
async def test_expired_resource_lease_cannot_be_renewed():
    now = [0.0]
    arbiter = ResourceArbiter(clock=lambda: now[0])
    leases = await arbiter.acquire(
        "sensor:camera:cam0",
        (ResourceRequirement("sensor:camera:cam0", ResourceMode.EXCLUSIVE),),
        ttl_seconds=1.0,
    )
    now[0] = 2.0
    with pytest.raises(ResourceBusy):
        await arbiter.renew(leases, ttl_seconds=5.0)
