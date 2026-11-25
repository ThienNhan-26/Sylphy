import asyncio

import pytest

from sylphy_entity.contracts.events import EntityEvent
from sylphy_entity.runtime.event_loop import EntityEventLoop


@pytest.mark.asyncio
async def test_event_wakes_loop_without_waiting_for_tick() -> None:
    received = asyncio.Event()
    tick_dts: list[float] = []

    async def on_event(event: EntityEvent) -> None:
        assert event.event_type == "test.changed"
        received.set()

    async def on_tick(dt: float) -> None:
        tick_dts.append(dt)

    loop = EntityEventLoop(on_event=on_event, on_tick=on_tick, tick_interval_seconds=2.0)
    await loop.start()
    try:
        await loop.publish(EntityEvent(event_type="test.changed", source="pytest"))
        await asyncio.wait_for(received.wait(), timeout=0.5)
        assert loop.metrics.events_processed == 1
        assert loop.metrics.last_event_id is not None
    finally:
        await loop.stop()
    assert not loop.running


@pytest.mark.asyncio
async def test_loop_ticks_at_configured_low_frequency() -> None:
    ticked = asyncio.Event()

    async def on_event(_: EntityEvent) -> None:
        pass

    async def on_tick(_: float) -> None:
        ticked.set()

    loop = EntityEventLoop(on_event=on_event, on_tick=on_tick, tick_interval_seconds=0.05)
    await loop.start()
    try:
        await asyncio.wait_for(ticked.wait(), timeout=0.5)
        assert loop.metrics.ticks >= 1
    finally:
        await loop.stop()
