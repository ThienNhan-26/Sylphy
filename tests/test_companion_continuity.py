"""Turn 5 continuity and state recovery tests.

These tests verify that Companion State is durable, recoverable, and truthful
across a Runtime restart. They use temporary SQLite databases, a fake provider,
and no live model or network.

Durable state is rebuilt from the database at the deterministic boundary
(record_completed_turn / restore_companion_state), never carried over from a
previous Runtime instance.
"""

import logging
import sqlite3
from datetime import datetime, timezone

import pytest

from sylphy.companion_slice import (
    SliceResult,
    _turn_event_id,
    record_completed_turn,
    restore_companion_state,
)
from sylphy.companion_contract import CompanionState, OutcomeStatus
from sylphy.config import load_config
from sylphy.context import ContextBuilder
from sylphy.core import ConversationService, Message, Turn
from sylphy.interaction_policy import InteractionAction, PolicyConditions
from sylphy.runtime import Runtime
from sylphy.storage import SQLiteStore


class FakeProvider:
    def __init__(self, answer="hello", failure=None):
        self.answer = answer
        self.failure = failure
        self.messages = None

    def reply(self, messages, cancel=None, on_chunk=None):
        self.messages = messages
        if self.failure:
            raise self.failure
        if cancel and cancel.is_set():
            raise InterruptedError("cancelled")
        if on_chunk:
            on_chunk(self.answer)
        return self.answer


def make_store_service(tmp_path, answer="hello", failure=None):
    store = SQLiteStore(tmp_path / "continuity.db")
    session = store.create_session("continuity-session")
    service = ConversationService(
        FakeProvider(answer, failure), store, ContextBuilder("system", "identity", 10000)
    )
    return store, session, service


def start_runtime(companion_enabled=False):
    runtime = Runtime("local-model", heartbeat_enabled=False)
    runtime.companion_enabled = companion_enabled
    runtime.start()
    return runtime


def persist_turn(store, session_id, answer="hello"):
    now = datetime.now(timezone.utc)
    user = Message("u1", session_id, "user", "hi", now)
    assistant = Message("a1", session_id, "assistant", answer, now)
    store.append_turn(user, assistant)
    return Turn(session_id, user, assistant)


# CT-01 ----------------------------------------------------------------------


def test_ct01_fresh_database_produces_valid_initial_state(tmp_path):
    store = SQLiteStore(tmp_path / "fresh.db")

    # No companion state has been recorded yet.
    assert store.get_companion_state() is None

    restored = restore_companion_state(store)
    assert restored.revision == 0
    assert restored.last_event_id is None
    assert restored.last_outcome_id is None
    assert restored.last_observed_at is None
    assert restored.conversation_active is False
    assert restored.pending_presence_notices == 0

    # No historical event is fabricated and no active conversation is asserted.
    assert store.list_companion_outcomes() == []


# CT-02 ----------------------------------------------------------------------


def test_ct02_completed_turn_persists_state_and_outcome_agree(tmp_path):
    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=True)
    result = runtime.run_conversation(service, session.id, "hello", persistence=store)

    event_id = _turn_event_id(session.id, result.assistant.id)

    outcome_row = store.get_companion_outcome(event_id)
    assert outcome_row is not None
    # (outcome_id, event_id, decision_id, action_id, status, detail, evidence, created_at)
    assert outcome_row[1] == event_id
    assert outcome_row[4] == OutcomeStatus.SUCCEEDED.value
    assert outcome_row[3] is not None  # action_id present for executed outcome
    assert outcome_row[6] == result.assistant.id  # evidence = message id only

    state_row = store.get_companion_state()
    assert state_row is not None
    # (revision, last_event_id, last_outcome_id, last_observed_at, conversation_active, pending_presence_notices)
    assert state_row[0] == 1  # revision incremented exactly once
    assert state_row[1] == event_id  # state references the recorded outcome's event
    assert state_row[2] == outcome_row[0]  # last_outcome_id == outcome id
    assert state_row[4] == 0  # conversation_active = False after the turn
    assert state_row[5] == 0  # no pending presence notices in this test

    # Durable state and durable outcome agree.
    assert state_row[2] == outcome_row[0]


# CT-03 ----------------------------------------------------------------------


def test_ct03_runtime_restart_recovers_durable_state(tmp_path):
    db_path = tmp_path / "restart.db"
    store = SQLiteStore(db_path)
    session = store.create_session("restart-session")
    service = ConversationService(
        FakeProvider("first reply"), store, ContextBuilder("system", "identity", 10000)
    )

    runtime_a = start_runtime(companion_enabled=True)
    result_a = runtime_a.run_conversation(
        service, session.id, "first", persistence=store
    )
    runtime_a.shutdown()

    state_after_a = store.get_companion_state()
    assert state_after_a is not None
    assert state_after_a[0] == 1  # revision recorded by Runtime A
    event_id_a = _turn_event_id(session.id, result_a.assistant.id)
    assert state_after_a[1] == event_id_a

    # Runtime B is a brand-new instance against the SAME database. It must
    # recover durable state from the database, not from Runtime A's memory.
    runtime_b = start_runtime(companion_enabled=True)
    result_b = runtime_b.run_conversation(
        service, session.id, "second", persistence=store
    )
    runtime_b.shutdown()

    state_after_b = store.get_companion_state()
    assert state_after_b is not None
    assert state_after_b[0] == 2  # revision continued from durable state, not reset
    event_id_b = _turn_event_id(session.id, result_b.assistant.id)
    assert state_after_b[1] == event_id_b  # latest event is Runtime B's turn
    assert state_after_b[1] != event_id_a  # distinct events
    assert state_after_b[2] is not None  # last_outcome_id recorded

    # Two durable outcomes, one per completed turn.
    outcomes = store.list_companion_outcomes()
    assert len(outcomes) == 2
    outcome_event_ids = {row[1] for row in outcomes}
    assert outcome_event_ids == {event_id_a, event_id_b}
    assert all(row[4] == OutcomeStatus.SUCCEEDED.value for row in outcomes)


# CT-04 ----------------------------------------------------------------------


def test_ct04_multiple_turns_revision_progression_and_history(tmp_path):
    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=True)

    result1 = runtime.run_conversation(service, session.id, "first", persistence=store)
    result2 = runtime.run_conversation(service, session.id, "second", persistence=store)
    result3 = runtime.run_conversation(service, session.id, "third", persistence=store)

    events = {
        _turn_event_id(session.id, result1.assistant.id),
        _turn_event_id(session.id, result2.assistant.id),
        _turn_event_id(session.id, result3.assistant.id),
    }
    assert len(events) == 3  # distinct turn -> distinct event id

    outcomes = store.list_companion_outcomes()
    assert len(outcomes) == 3
    assert all(row[4] == OutcomeStatus.SUCCEEDED.value for row in outcomes)

    state_row = store.get_companion_state()
    assert state_row[0] == 3  # revision incremented once per distinct turn
    assert state_row[1] == _turn_event_id(session.id, result3.assistant.id)
    assert state_row[2] is not None

    # Historical outcomes remain accessible; the latest outcome id is reflected
    # in durable state.
    latest_outcome_id = outcomes[0][0]  # newest first
    assert state_row[2] == latest_outcome_id


# CT-05 ----------------------------------------------------------------------


def test_ct05_duplicate_event_is_idempotent_and_state_consistent(tmp_path):
    db_path = tmp_path / "duplicate.db"
    store = SQLiteStore(db_path)
    session = store.create_session("duplicate-session")
    now = datetime.now(timezone.utc)
    turn = persist_turn(store, session.id, "hello")

    # Processing the same event twice must not duplicate the outcome or the
    # revision increment.
    first = record_completed_turn(
        store, session_id=session.id, turn=turn, occurred_at=now
    )
    second = record_completed_turn(
        store, session_id=session.id, turn=turn, occurred_at=now
    )

    assert first.skipped_duplicate is False
    assert second.skipped_duplicate is True
    assert len(store.list_companion_outcomes()) == 1  # one durable outcome
    state_row = store.get_companion_state()
    assert state_row[0] == 1  # revision incremented once, not twice

    # Simulate a restart: a fresh store handle against the same database
    # restores the same consistent state (no duplicate, no invented revision).
    store2 = SQLiteStore(db_path)
    restored = restore_companion_state(store2)
    assert restored.revision == 1
    assert restored.last_event_id == first.outcome.event_id
    assert restored.last_outcome_id == first.outcome.outcome_id
    assert restored.conversation_active is False


# CT-06 ----------------------------------------------------------------------


def test_ct06_interrupted_bookkeeping_no_invented_outcome(tmp_path, monkeypatch):
    store, session, service = make_store_service(tmp_path, answer="assistant reply")
    runtime = start_runtime(companion_enabled=True)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("bookkeeping down")

    monkeypatch.setattr(store, "record_companion_outcome", boom)

    result = runtime.run_conversation(service, session.id, "hello", persistence=store)

    # Conversation remains valid and persisted despite bookkeeping failure.
    messages = store.messages(session.id)
    assert len(messages) == 2
    assert messages[1].content == "assistant reply"

    # No companion outcome row and no state row were left behind.
    assert store.get_companion_outcome(_turn_event_id(session.id, result.assistant.id)) is None
    assert store.get_companion_state() is None

    # Restart against the same database does not invent a missing outcome:
    # a fresh Runtime records the next turn from a clean durable state.
    monkeypatch.undo()
    runtime_b = start_runtime(companion_enabled=True)
    result_b = runtime_b.run_conversation(
        service, session.id, "after failure", persistence=store
    )
    runtime_b.shutdown()

    state_after_b = store.get_companion_state()
    assert state_after_b is not None
    assert state_after_b[0] == 1  # clean revision from the restored empty state
    assert state_after_b[1] == _turn_event_id(session.id, result_b.assistant.id)
    outcomes = store.list_companion_outcomes()
    assert len(outcomes) == 1  # only the successful turn after the failure


# CT-07 ----------------------------------------------------------------------


def test_ct07_ephemeral_state_reset_behavior(tmp_path):
    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=True)
    runtime.run_conversation(service, session.id, "hello", persistence=store)

    # conversation_active is a historical snapshot: it is False after every
    # completed turn and never survives restart as a claim of current activity.
    state_row = store.get_companion_state()
    assert state_row[4] == 0  # conversation_active = False

    # pending_presence_notices is a snapshot taken at turn completion, not a
    # claim that anything is currently pending or being monitored.
    assert state_row[5] == 0

    # A brand-new Runtime against the same database restores the historical
    # snapshot; it does not reactivate conversation or presence.
    runtime_b = start_runtime(companion_enabled=True)
    restored = restore_companion_state(store)
    runtime_b.shutdown()

    assert restored.conversation_active is False
    assert restored.pending_presence_notices == 0

    # Fresh in-process state resets to defaults (no ephemeral activity carried in).
    fresh = CompanionState()
    assert fresh.conversation_active is False
    assert fresh.pending_presence_notices == 0
    assert fresh.last_event_id is None
    assert fresh.revision == 0


# CT-08 ----------------------------------------------------------------------


def test_ct08_truthfulness_restored_state_no_continuous_awareness(tmp_path):
    store, session, service = make_store_service(tmp_path)
    occurred_at = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)

    now = occurred_at
    user = Message("u1", session.id, "user", "hi", now)
    assistant = Message("a1", session.id, "assistant", "hello", now)
    store.append_turn(user, assistant)
    turn = Turn(session.id, user, assistant)

    runtime = start_runtime(companion_enabled=True)
    result = record_completed_turn(
        store, session_id=session.id, turn=turn, occurred_at=occurred_at
    )
    runtime.shutdown()

    # Restored state describes an actual recorded event, not current awareness.
    restored = restore_companion_state(store)
    assert restored.last_observed_at == occurred_at
    assert restored.last_event_id == result.outcome.event_id
    assert restored.last_outcome_id == result.outcome.outcome_id
    assert restored.conversation_active is False

    # The observation timestamp is in the past relative to "now"; restoring it
    # must not imply continuous awareness or activity during downtime.
    assert restored.last_observed_at < datetime.now(timezone.utc)

    # No awareness, emotion, waiting, or offline-activity field exists.
    state = CompanionState()
    for forbidden in (
        "awareness",
        "consciousness",
        "emotion",
        "waiting",
        "offline_activity",
        "continuous_presence",
        "continuous_awareness",
    ):
        assert not hasattr(state, forbidden), f"state must not expose {forbidden}"

    # Restored state is historical: it cannot be read as ongoing monitoring.
    assert store.get_companion_state()[4] == 0  # conversation not active


# CT-09 ----------------------------------------------------------------------


def test_ct09_feature_disabled_normal_conversation_no_companion_state(tmp_path):
    config_file = tmp_path / "app.toml"
    config_file.write_text(
        '[sylphy]\ndata_dir="state"\n[ollama]\nmodel="local"\n[companion]\nenabled=false\n',
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert config.companion_enabled is False

    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=False)
    result = runtime.run_conversation(service, session.id, "hello", persistence=store)

    # Normal conversation works and is persisted.
    messages = store.messages(session.id)
    assert len(messages) == 2
    assert messages[1].id == result.assistant.id

    # Companion state is not modified when the feature is disabled.
    assert store.list_companion_outcomes() == []
    assert store.get_companion_state() is None

    # Startup restoration does not activate Companion behavior: no row exists
    # to restore, and restoration performs no companion-side activation.
    restored = restore_companion_state(store)
    assert restored.revision == 0
    assert restored.last_event_id is None
    assert restored.conversation_active is False


# CT-10 ----------------------------------------------------------------------


def test_ct10_transaction_rollback_and_state_outcome_consistency(tmp_path, monkeypatch):
    store, session, service = make_store_service(tmp_path)
    now = datetime.now(timezone.utc)
    user = Message("u1", session.id, "user", "hi", now)
    assistant = Message("a1", session.id, "assistant", "hello", now)
    store.append_turn(user, assistant)
    turn = Turn(session.id, user, assistant)

    original_connect = store._connect

    def failing_connect():
        return _FailingConnection(original_connect())

    monkeypatch.setattr(store, "_connect", failing_connect)

    with pytest.raises(sqlite3.OperationalError, match="simulated disk failure"):
        record_completed_turn(
            store, session_id=session.id, turn=turn, occurred_at=now
        )

    # Atomic rollback: no outcome row, no state row.
    assert store.get_companion_outcome(_turn_event_id(session.id, turn.assistant.id)) is None
    assert store.get_companion_state() is None

    # A subsequent successful turn against the same database is consistent:
    # state.last_outcome_id matches the latest outcome row, revision matches the
    # number of successful outcomes.
    monkeypatch.undo()
    runtime = start_runtime(companion_enabled=True)
    runtime.run_conversation(
        service, session.id, "after rollback", persistence=store
    )
    runtime.shutdown()

    outcomes = store.list_companion_outcomes()
    assert len(outcomes) == 1
    state_row = store.get_companion_state()
    assert state_row is not None
    assert state_row[0] == 1  # revision == number of successful outcomes
    assert state_row[1] == outcomes[0][1]  # state's last_event_id == outcome's event_id
    assert state_row[2] == outcomes[0][0]  # state's last_outcome_id == outcome's outcome_id


class _FailingConnection:
    """Wrapper around a sqlite3 connection that fails the companion state INSERT."""

    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._conn.commit()
            else:
                self._conn.rollback()
        except Exception:
            pass
        return False

    def execute(self, *args, **kwargs):
        sql = args[0] if args else ""
        if "companion_state" in sql and "INSERT" in sql.upper():
            raise sqlite3.OperationalError("simulated disk failure")
        return self._conn.execute(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)
