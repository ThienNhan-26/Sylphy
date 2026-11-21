from datetime import datetime, timezone
from threading import Event, Thread
import time

import pytest

from sylphy.core import Message, Turn
from sylphy.cognition import CognitionService, CognitionTask
from sylphy.memory import MemoryService
from sylphy.runtime import EventType, ResourceGovernor, Runtime, RuntimePhase
from sylphy.storage import SQLiteStore


def turn(session="s", suffix="1"):
    now = datetime.now(timezone.utc)
    return Turn(session, Message("u"+suffix, session, "user", "hi", now),
                Message("a"+suffix, session, "assistant", "hello", now))


def seed_turn(store, suffix="1"):
    session = store.create_session()
    completed = turn(session.id, suffix)
    store.append_turn(completed.user, completed.assistant)
    return completed


def test_task_queue_duplicate_capacity_and_bounded_retry(tmp_path):
    store = SQLiteStore(tmp_path / "tasks.db")
    one = store.enqueue_cognition_task("s", ("u", "a"), max_pending=1)
    assert store.enqueue_cognition_task("s", ("u", "a"), max_pending=1).id == one.id
    with pytest.raises(OverflowError):
        store.enqueue_cognition_task("s", ("u2", "a2"), max_pending=1)
    task = store.claim_cognition_task()
    assert task.status == "running" and task.attempt_count == 1
    store.finish_cognition_task(task.id, "temporary", max_attempts=2)
    task = store.claim_cognition_task()
    assert task.attempt_count == 2
    store.finish_cognition_task(task.id, "again", max_attempts=2)
    assert store.get_cognition_task(task.id).status == "failed"


def test_worker_success_empty_result_and_provider_failure_retry(tmp_path):
    store = SQLiteStore(tmp_path / "worker.db")
    completed = seed_turn(store)
    task = store.enqueue_cognition_task(completed.session_id, (completed.user.id, completed.assistant.id))
    class Cognition:
        def __init__(self, failure=False): self.calls=0; self.failure=failure
        def analyze(self, *_args, **_kwargs):
            self.calls += 1
            if self.failure: raise RuntimeError("provider down")
            return []
    cognition=Cognition()
    runtime=Runtime("fake",heartbeat_enabled=False)
    runtime.configure_cognition_worker(store,cognition,shutdown_grace_seconds=1)
    runtime.start()
    deadline=time.monotonic()+2
    while store.get_cognition_task(task.id).status != "completed" and time.monotonic()<deadline: Event().wait(.01)
    assert store.get_cognition_task(task.id).status == "completed"
    assert cognition.calls == 1
    runtime.shutdown()

    failed_store=SQLiteStore(tmp_path / "failed.db")
    failed_turn=seed_turn(failed_store)
    failed=failed_store.enqueue_cognition_task(failed_turn.session_id,(failed_turn.user.id,failed_turn.assistant.id))
    failing=Cognition(failure=True)
    runtime=Runtime("fake",heartbeat_enabled=False)
    runtime.configure_cognition_worker(failed_store,failing,shutdown_grace_seconds=1)
    runtime.start()
    deadline=time.monotonic()+2
    while failed_store.get_cognition_task(failed.id).status != "failed" and time.monotonic()<deadline: Event().wait(.01)
    assert failed_store.get_cognition_task(failed.id).status == "failed"
    assert failed_store.get_cognition_task(failed.id).attempt_count == 2
    assert "provider down" in failed_store.get_cognition_task(failed.id).last_error
    runtime.shutdown()


def test_malformed_model_output_retries_then_marks_task_failed(tmp_path):
    store = SQLiteStore(tmp_path / "invalid-output.db")
    completed = seed_turn(store)
    task = store.enqueue_cognition_task(completed.session_id, (completed.user.id, completed.assistant.id))
    class Provider:
        def reply(self, *_args, **_kwargs):
            return '{"unexpected":[]}'
    cognition = CognitionService(Provider(), store, MemoryService(store))
    runtime = Runtime("fake", heartbeat_enabled=False)
    runtime.configure_cognition_worker(store, cognition, shutdown_grace_seconds=1)
    runtime.start()
    deadline = time.monotonic() + 2
    while store.get_cognition_task(task.id).status != "failed" and time.monotonic() < deadline:
        Event().wait(.01)
    result = store.get_cognition_task(task.id)
    assert result.status == "failed"
    assert result.attempt_count == 2
    assert "CognitionResponseError" in result.last_error
    runtime.shutdown()


def test_recovery_requeues_interrupted_task_and_worker_processes_once(tmp_path):
    store=SQLiteStore(tmp_path / "recovery.db")
    t=seed_turn(store)
    task=store.enqueue_cognition_task(t.session_id,(t.user.id,t.assistant.id))
    assert store.claim_cognition_task().status == "running"
    class Cognition:
        calls=0
        def analyze(self, received, messages, **kwargs):
            self.calls+=1
            assert received.user.id == t.user.id and len(messages)==2
            return []
    cognition=Cognition(); runtime=Runtime("fake",heartbeat_enabled=False)
    runtime.configure_cognition_worker(store,cognition,shutdown_grace_seconds=1)
    runtime.start()
    deadline=time.monotonic()+2
    while store.get_cognition_task(task.id).status != "completed" and time.monotonic()<deadline: Event().wait(.01)
    assert store.get_cognition_task(task.id).status == "completed"
    assert cognition.calls == 1
    runtime.shutdown()


def test_response_returns_after_enqueue_before_cognition_finishes(tmp_path):
    store=SQLiteStore(tmp_path / "async.db")
    session=store.create_session()
    entered, release=Event(), Event()
    class Cognition:
        def analyze(self, *_args, **_kwargs): entered.set(); release.wait(); return []
    class Conversation:
        def respond(self, sid, text, **kwargs):
            t=turn(sid)
            store.append_turn(t.user,t.assistant)
            return t
    runtime=Runtime("fake",heartbeat_enabled=False)
    runtime.configure_cognition_worker(store,Cognition(),shutdown_grace_seconds=1)
    runtime.start()
    returned, result=Event(), []
    def converse():
        result.append(runtime.run_conversation(Conversation(),session.id,"hello"))
        returned.set()
    thread=Thread(target=converse)
    thread.start()
    try:
        assert entered.wait(1)
        assert returned.wait(1)
        assert result[0].assistant.content == "hello"
        assert not release.is_set()
        assert store.messages(session.id)
    finally:
        release.set()
        thread.join(1)
        runtime.shutdown()


def test_conversation_waiter_gets_inference_slot_before_next_cognition(tmp_path):
    store=SQLiteStore(tmp_path / "priority.db")
    first_turn=seed_turn(store,"1")
    second_turn=seed_turn(store,"2")
    conversation_session=store.create_session()
    store.enqueue_cognition_task(first_turn.session_id,(first_turn.user.id,first_turn.assistant.id))
    store.enqueue_cognition_task(second_turn.session_id,(second_turn.user.id,second_turn.assistant.id))
    first_entered, conversation_entered=Event(),Event()
    class Cognition:
        calls=0
        def analyze(self,*_a,cancel=None,**_k):
            self.calls+=1
            if self.calls==1:
                first_entered.set()
                while not cancel.is_set():
                    Event().wait(.01)
                raise InterruptedError("conversation has priority")
            return []
    runtime=Runtime("fake",heartbeat_enabled=False)
    runtime.configure_cognition_worker(store,Cognition(),shutdown_grace_seconds=1); runtime.start()
    assert first_entered.wait(1)
    class Conversation:
        def respond(self, session_id,*_a,**_k):
            completed = turn(session_id, "conversation")
            store.append_turn(completed.user, completed.assistant)
            conversation_entered.set()
            return completed
    thread=Thread(target=lambda: runtime.run_conversation(Conversation(),conversation_session.id,"hi"))
    thread.start()
    assert conversation_entered.wait(1)
    thread.join(1); runtime.shutdown()


def test_shutdown_timeout_does_not_report_stopped_and_pending_survives(tmp_path):
    store=SQLiteStore(tmp_path / "shutdown.db")
    completed=seed_turn(store)
    task=store.enqueue_cognition_task(completed.session_id,(completed.user.id,completed.assistant.id))
    entered, release=Event(),Event()
    class Cognition:
        def analyze(self,*_a,**_k): entered.set(); release.wait(3); return []
    runtime=Runtime("fake",heartbeat_enabled=True,heartbeat_interval_seconds=60)
    runtime.configure_cognition_worker(store,Cognition(),shutdown_grace_seconds=.05); runtime.start()
    assert entered.wait(1)
    with pytest.raises(TimeoutError): runtime.shutdown()
    assert runtime.state.snapshot().phase == RuntimePhase.ERROR
    assert runtime.heartbeat is not None and not runtime.heartbeat._thread.is_alive()
    assert runtime.state.snapshot().active_background_tasks == 0
    assert store.get_cognition_task(task.id).status == "running"
    release.set()
    if runtime.cognition_worker._thread: runtime.cognition_worker._thread.join(1)


def test_enqueue_failure_does_not_invalidate_persisted_response(tmp_path):
    store = SQLiteStore(tmp_path / "enqueue-failure.db")
    session = store.create_session()
    completed = turn(session.id)

    class Conversation:
        def respond(self, *_args, **_kwargs):
            store.append_turn(completed.user, completed.assistant)
            return completed

    class BrokenQueue:
        running = False
        current_task = None
        def enqueue(self, _turn):
            raise OverflowError("queue full")
        def start(self):
            return 0
        def stop(self):
            return True

    runtime = Runtime("fake", heartbeat_enabled=False)
    runtime.cognition_worker = BrokenQueue()
    runtime.start()
    assert runtime.run_conversation(Conversation(), session.id, "hello") == completed
    assert [message.id for message in store.messages(session.id)] == [completed.user.id, completed.assistant.id]
    runtime.shutdown()


def test_cognition_worker_start_is_idempotent(tmp_path):
    store = SQLiteStore(tmp_path / "idempotent-start.db")
    class Cognition:
        def analyze(self, *_args, **_kwargs):
            return []
    runtime = Runtime("fake", heartbeat_enabled=False)
    runtime.configure_cognition_worker(store, Cognition(), shutdown_grace_seconds=1)
    worker = runtime.cognition_worker
    try:
        worker.start()
        first_thread = worker._thread
        assert worker.start() == 0
        assert worker._thread is first_thread
        assert worker.running
    finally:
        worker.stop()


def test_worker_clears_stale_error_after_task_recovers(tmp_path):
    store = SQLiteStore(tmp_path / "worker-recovery-error.db")
    completed = seed_turn(store, "recover")
    task = store.enqueue_cognition_task(completed.session_id, (completed.user.id, completed.assistant.id))

    class FlakyCognition:
        calls = 0
        def analyze(self, *_args, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary provider failure")
            return []

    runtime = Runtime("fake", heartbeat_enabled=False)
    cognition = FlakyCognition()
    runtime.configure_cognition_worker(store, cognition, shutdown_grace_seconds=1)
    runtime.start()
    deadline = time.monotonic() + 3
    while store.get_cognition_task(task.id).status != "completed" and time.monotonic() < deadline:
        Event().wait(.01)
    assert store.get_cognition_task(task.id).status == "completed"
    assert cognition.calls == 2
    assert runtime.cognition_worker.last_error is None
    runtime.shutdown()
