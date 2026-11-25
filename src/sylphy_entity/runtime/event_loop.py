"""Event-driven Entity loop with a low-frequency timed tick.

This loop owns scheduling only. It does not contain Brain policy or I/O logic.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

from sylphy_entity.contracts.events import EntityEvent

logger = logging.getLogger(__name__)
EventHandler = Callable[[EntityEvent], Awaitable[None]]
TickHandler = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class EventLoopMetrics:
    events_received: int
    events_processed: int
    ticks: int
    last_tick_monotonic: float | None
    last_event_id: str | None


class EntityEventLoop:
    """Wake immediately for events and tick at a low fixed cadence."""

    def __init__(
        self,
        *,
        on_event: EventHandler,
        on_tick: TickHandler,
        tick_interval_seconds: float = 1.0,
    ) -> None:
        if tick_interval_seconds <= 0:
            raise ValueError("tick_interval_seconds must be greater than zero")
        self._on_event = on_event
        self._on_tick = on_tick
        self._tick_interval = tick_interval_seconds
        self._queue: asyncio.Queue[EntityEvent] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._events_received = 0
        self._events_processed = 0
        self._ticks = 0
        self._last_tick: float | None = None
        self._last_event_id: str | None = None
        self._last_tick_start: float | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def metrics(self) -> EventLoopMetrics:
        return EventLoopMetrics(
            events_received=self._events_received,
            events_processed=self._events_processed,
            ticks=self._ticks,
            last_tick_monotonic=self._last_tick,
            last_event_id=self._last_event_id,
        )

    async def publish(self, event: EntityEvent) -> None:
        if not self.running:
            raise RuntimeError("Entity event loop is not running")
        self._events_received += 1
        await self._queue.put(event)

    async def start(self) -> None:
        if self.running:
            return
        self._stop_event.clear()
        self._last_tick_start = time.monotonic()
        self._task = asyncio.create_task(self._run(), name="sylphy-entity-event-loop")

    async def stop(self, *, drain_timeout_seconds: float = 5.0) -> None:
        if drain_timeout_seconds <= 0:
            raise ValueError("drain_timeout_seconds must be positive")
        if self._task is None:
            return
        # The API and adapters are stopped before this method is called, so
        # the queue is now finite. Drain committed events before stopping.
        try:
            await asyncio.wait_for(self._queue.join(), timeout=drain_timeout_seconds)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("Entity event queue did not drain before shutdown") from exc
        self._stop_event.set()
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        next_tick = loop.time() + self._tick_interval
        while not self._stop_event.is_set():
            timeout = max(0.0, next_tick - loop.time())
            event: EntityEvent | None = None
            try:
                event = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                pass

            if event is not None:
                try:
                    await self._on_event(event)
                    self._events_processed += 1
                    self._last_event_id = event.event_id
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # An individual event handler failure is contained. The
                    # daemon remains alive and the failure remains visible.
                    logger.exception("Entity event handler failed: %s", event.event_id)
                finally:
                    self._queue.task_done()

            now = loop.time()
            if now >= next_tick:
                previous = self._last_tick_start if self._last_tick_start is not None else now
                dt = max(0.0, now - previous)
                self._last_tick_start = now
                self._last_tick = time.monotonic()
                try:
                    await self._on_tick(dt)
                    self._ticks += 1
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Entity timed tick failed")
                # Do not run a burst of catch-up ticks after a delayed callback.
                next_tick = loop.time() + self._tick_interval
