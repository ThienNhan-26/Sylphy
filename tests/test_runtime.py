from dataclasses import FrozenInstanceError
from threading import Event, Thread
import time

import pytest

from sylphy.core import ConversationCancelled
from sylphy.runtime import (
    EventBus, EventType, Heartbeat, ResourceGovernor, Runtime, RuntimePhase, RuntimeState,
)


def test_event_bus_multiple_subscribers_unsubscribe_and_isolate_errors(caplog):
    bus = EventBus()
    received = []
    bus.subscribe(EventType.HEARTBEAT, lambda event: (_ for _ in ()).throw(ValueError("handler")))
    remove = bus.subscribe(EventType.HEARTBEAT, received.append)
    bus.publish(EventType.HEARTBEAT, {"tick": 7})
    assert len(received) == 1
    assert received[0].name == EventType.HEARTBEAT
    assert received[0].payload == {"tick": 7}
    assert received[0].timestamp.tzinfo is not None
    assert "Event handler failed" in caplog.text
    remove()
    bus.publish(EventType.HEARTBEAT)
    assert len(received) == 1


def test_runtime_lifecycle_transitions_startup_failure_and_repeated_shutdown():
    runtime = Runtime("local-model", heartbeat_enabled=False)
    transitions = []
    runtime.events.subscribe(EventType.RUNTIME_STATE_CHANGED, transitions.append)
    with pytest.raises(RuntimeError, match="Invalid runtime transition"):
        runtime.transition(RuntimePhase.IDLE)
    runtime.start()
    assert runtime.state.snapshot().phase == RuntimePhase.READY
    runtime.shutdown()
    runtime.shutdown()
    assert runtime.state.snapshot().phase == RuntimePhase.STOPPED
    assert [(event.payload["from"], event.payload["to"]) for event in transitions] == [
        ("STOPPED", "STARTING"), ("STARTING", "READY"),
        ("READY", "SHUTTING_DOWN"), ("SHUTTING_DOWN", "STOPPED"),
    ]

    failed = Runtime("local-model", heartbeat_enabled=False)
    with pytest.raises(ValueError, match="init failed"):
        failed.start(lambda: (_ for _ in ()).throw(ValueError("init failed")))
    assert failed.state.snapshot().phase == RuntimePhase.ERROR
    assert "init failed" in failed.state.snapshot().last_error
    failed.shutdown()


def test_runtime_state_snapshots_are_read_only_and_track_activity():
    state = RuntimeState("model")
    before = state.snapshot()
    state.set_session("session-1")
    state.begin_operation("generation")
    active = state.snapshot()
    assert active.session_id == "session-1" and active.generation_active
    assert active.active_operation == "generation"
    assert active.last_activity >= before.last_activity
    with pytest.raises(FrozenInstanceError):
        active.model = "other"
    state.end_operation()
    assert not state.snapshot().generation_active


def test_heartbeat_emits_and_stops_and_disabled_needs_no_thread():
    bus = EventBus()
    state = RuntimeState("model")
    received = Event()
    bus.subscribe(EventType.HEARTBEAT, lambda event: received.set())
    heartbeat = Heartbeat(0.02, bus, state)
    heartbeat.start()
    assert received.wait(1)
    heartbeat.stop()
    assert state.snapshot().active_background_tasks == 0
    assert heartbeat._thread is not None and not heartbeat._thread.is_alive()
    runtime = Runtime("model", heartbeat_enabled=False)
    runtime.start()
    assert runtime.heartbeat is None
    runtime.shutdown()


def test_resource_governor_serializes_and_releases_after_error():
    governor = ResourceGovernor(1)
    entered = Event()
    release = Event()
    order = []

    def first():
        with governor.generation():
            entered.set()
            release.wait(2)
            order.append("first")

    def second():
        entered.wait(1)
        with governor.generation():
            order.append("second")

    one, two = Thread(target=first), Thread(target=second)
    one.start(); two.start()
    assert entered.wait(1)
    time.sleep(0.03)
    assert order == []
    release.set()
    one.join(1); two.join(1)
    assert order == ["first", "second"]
    with pytest.raises(ValueError):
        with governor.generation():
            raise ValueError("failure")
    with governor.generation():
        pass


def test_resource_wait_cancellation_does_not_leak_slot():
    governor = ResourceGovernor(1)
    held = Event()
    release = Event()

    def holder():
        with governor.generation():
            held.set()
            release.wait(2)

    thread = Thread(target=holder)
    thread.start()
    assert held.wait(1)
    cancel = Event(); cancel.set()
    with pytest.raises(InterruptedError):
        with governor.generation(cancel):
            pass
    release.set(); thread.join(1)
    with governor.generation():
        pass


def test_runtime_wraps_existing_conversation_and_restores_idle_on_all_paths():
    runtime = Runtime("model", heartbeat_enabled=False)
    events = []
    runtime.events.subscribe(EventType.RESPONSE_COMPLETED, events.append)
    runtime.start()

    class Service:
        def respond(self, session_id, text, cancel=None, on_chunk=None):
            return text.upper()

    assert runtime.run_conversation(Service(), "s1", "hello") == "HELLO"
    snapshot = runtime.state.snapshot()
    assert snapshot.phase == RuntimePhase.IDLE and not snapshot.generation_active
    assert events and events[0].payload["session_id"] == "s1"

    class Failure:
        def respond(self, *args, **kwargs):
            raise RuntimeError("model down")

    with pytest.raises(RuntimeError):
        runtime.run_conversation(Failure(), "s1", "hello")
    assert runtime.state.snapshot().phase == RuntimePhase.IDLE
    assert runtime.state.snapshot().last_error == "RuntimeError: model down"

    class Cancel:
        def respond(self, *args, **kwargs):
            raise ConversationCancelled("cancelled")

    with pytest.raises(ConversationCancelled):
        runtime.run_conversation(Cancel(), "s1", "hello")
    assert runtime.state.snapshot().phase == RuntimePhase.IDLE
    runtime.shutdown()


def test_cancelled_waiter_does_not_clear_active_generation_state():
    runtime = Runtime("model", heartbeat_enabled=False)
    runtime.start()
    entered, release = Event(), Event()

    class Blocking:
        def respond(self, *args, **kwargs):
            entered.set()
            release.wait(2)
            return None

    thread = Thread(target=lambda: runtime.run_conversation(Blocking(), "s1", "one"))
    thread.start()
    assert entered.wait(1)
    assert runtime.state.snapshot().generation_active
    cancel = Event(); cancel.set()
    with pytest.raises(InterruptedError):
        runtime.run_conversation(Blocking(), "s1", "two", cancel=cancel)
    assert runtime.state.snapshot().generation_active
    release.set(); thread.join(1)
    assert runtime.state.snapshot().phase == RuntimePhase.IDLE
    assert not runtime.state.snapshot().generation_active
    runtime.shutdown()


@pytest.mark.parametrize("failure", [None, RuntimeError("failed")])
def test_shutdown_waits_for_active_generation_and_releases_slot(failure):
    runtime = Runtime("model", heartbeat_enabled=False)
    runtime.start()
    entered, release, shutdown_done = Event(), Event(), Event()
    shutting_down = Event()
    runtime.events.subscribe(EventType.RUNTIME_SHUTTING_DOWN, lambda event: shutting_down.set())

    class BlockingService:
        def respond(self, *args, **kwargs):
            entered.set()
            release.wait(2)
            if failure:
                raise failure
            return None

    worker = Thread(target=lambda: _capture_error(runtime, BlockingService()))
    worker.start()
    assert entered.wait(1)
    shutdown_thread = Thread(target=lambda: (runtime.shutdown(), shutdown_done.set()))
    shutdown_thread.start()
    # shutdown has transitioned, but it must not report STOPPED while the operation is held.
    assert shutting_down.wait(1)
    assert runtime.state.snapshot().phase == RuntimePhase.SHUTTING_DOWN
    assert runtime.state.snapshot().generation_active
    assert not shutdown_done.is_set()
    release.set()
    worker.join(2); shutdown_thread.join(2)
    assert shutdown_done.is_set()
    assert runtime.state.snapshot().phase == RuntimePhase.STOPPED
    assert runtime.state.snapshot().active_operation is None
    with runtime.resources.generation():
        pass


def _capture_error(runtime, service):
    try:
        runtime.run_conversation(service, "shutdown-session", "message")
    except RuntimeError:
        pass


def test_shutdown_after_completion_and_repeated_shutdown_are_safe():
    runtime = Runtime("model", heartbeat_enabled=False)
    runtime.start()

    class Completed:
        def respond(self, *args, **kwargs):
            return None

    runtime.run_conversation(Completed(), "s", "text")
    runtime.shutdown()
    runtime.shutdown()
    assert runtime.state.snapshot().phase == RuntimePhase.STOPPED
    assert not runtime.state.snapshot().generation_active


def test_cognition_worker_start_is_idempotent_and_restart_recovers():
    from sylphy.runtime import CognitionWorker

    class Store:
        recoveries = 0
        def recover_cognition_tasks(self):
            self.recoveries += 1
            return 2
        def claim_cognition_task(self):
            return None

    store = Store()
    worker = CognitionWorker(store, object(), ResourceGovernor(), shutdown_grace_seconds=1)
    assert worker.start() == 2
    assert worker.start() == 0
    assert store.recoveries == 1
    assert worker.stop()
    assert worker.start() == 2
    assert store.recoveries == 2
    assert worker.stop()


def test_exploration_worker_start_is_idempotent_and_restart_recovers():
    from sylphy.runtime import ExplorationWorker

    class Store:
        recoveries = 0
        def recover_exploration_tasks(self, expiry_days):
            self.recoveries += 1
            return 3
        def claim_exploration_task(self, *args):
            return None, 0

    store = Store()
    worker = ExplorationWorker(
        store, object(), ResourceGovernor(), max_queue_items=5, cooldown_seconds=0,
        max_attempts_per_period=1, period_seconds=60, expiry_days=30,
        shutdown_grace_seconds=1,
    )
    assert worker.start() == 3
    assert worker.start() == 0
    assert store.recoveries == 1
    assert worker.stop()
    assert worker.start() == 3
    assert store.recoveries == 2
    assert worker.stop()
