"""CRI-1 vertical slice tests: completed conversation turn -> durable outcome -> bounded state.

These tests exercise the integrated slice end to end through Runtime.run_conversation()
and directly through record_completed_turn(). They use a fake provider and temporary
SQLite databases; no live model and no network are required. Deterministic boundaries
only - no LLM personality assertions.
"""

import logging
import sqlite3
from datetime import datetime, timezone

import pytest

from sylphy.companion_slice import SliceResult, _turn_event_id, record_completed_turn
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
    store = SQLiteStore(tmp_path / "slice.db")
    session = store.create_session("slice-session")
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


# VS-01 ---------------------------------------------------------------------


def test_vs01_successful_turn_creates_one_durable_outcome(tmp_path):
    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=True)
    result = runtime.run_conversation(service, session.id, "hello", persistence=store)

    rows = store.list_companion_outcomes()
    assert len(rows) == 1
    row = rows[0]
    # (outcome_id, event_id, decision_id, action_id, status, detail, evidence, created_at)
    assert row[4] == OutcomeStatus.SUCCEEDED.value
    assert row[3] is not None  # action_id present for executed outcome
    assert row[6] == result.assistant.id  # evidence is the assistant message id only
    assert row[1] == _turn_event_id(session.id, result.assistant.id)
    assert result.assistant.content not in (row[5] or "")  # no message text in detail


# VS-02 ---------------------------------------------------------------------


def test_vs02_state_updated_only_after_successful_outcome(tmp_path):
    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=True)
    result = runtime.run_conversation(service, session.id, "hello", persistence=store)

    row = store.get_companion_state()
    assert row is not None
    # (revision, last_event_id, last_outcome_id, last_observed_at, conversation_active, pending_presence_notices)
    assert row[0] == 1  # revision incremented exactly once
    assert row[1] == _turn_event_id(session.id, result.assistant.id)
    assert row[2] is not None  # last_outcome_id
    assert row[4] == 0  # conversation_active = False after the turn
    assert row[5] == 0  # no presence notices in this test


# VS-03 ---------------------------------------------------------------------


def test_vs03_idempotent_same_event_not_duplicated(tmp_path):
    store, session, _service = make_store_service(tmp_path)
    now = datetime.now(timezone.utc)
    turn = persist_turn(store, session.id, "hello")

    first = record_completed_turn(
        store, session_id=session.id, turn=turn, occurred_at=now, pending_presence_notices=0
    )
    second = record_completed_turn(
        store, session_id=session.id, turn=turn, occurred_at=now, pending_presence_notices=0
    )

    assert first.skipped_duplicate is False
    assert second.skipped_duplicate is True
    assert store.list_companion_outcomes() == [store.get_companion_outcome(first.outcome.event_id)]
    assert len(store.list_companion_outcomes()) == 1
    state_row = store.get_companion_state()
    assert state_row[0] == 1  # revision incremented once, not twice


# VS-04 ---------------------------------------------------------------------


def test_vs04_distinct_turns_two_outcomes_and_progression(tmp_path):
    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=True)
    runtime.run_conversation(service, session.id, "first", persistence=store)
    runtime.run_conversation(service, session.id, "second", persistence=store)

    rows = store.list_companion_outcomes()
    assert len(rows) == 2
    assert rows[0][1] != rows[1][1]  # distinct event ids
    assert rows[0][4] == OutcomeStatus.SUCCEEDED.value
    assert rows[1][4] == OutcomeStatus.SUCCEEDED.value
    state_row = store.get_companion_state()
    assert state_row[0] == 2  # revision incremented once per distinct turn


# VS-05 ---------------------------------------------------------------------


def test_vs05_provider_failure_no_successful_outcome(tmp_path):
    store, session, service = make_store_service(tmp_path, failure=RuntimeError("offline"))
    runtime = start_runtime(companion_enabled=True)

    with pytest.raises(RuntimeError, match="offline"):
        runtime.run_conversation(service, session.id, "hello", persistence=store)

    assert store.messages(session.id) == []  # turn never persisted
    assert store.list_companion_outcomes() == []  # slice never reached


# VS-06 ---------------------------------------------------------------------


def test_vs06_persistence_failure_does_not_trigger_slice(tmp_path, monkeypatch):
    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=True)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(store, "append_turn", boom)
    with pytest.raises(sqlite3.OperationalError, match="disk full"):
        runtime.run_conversation(service, session.id, "hello", persistence=store)

    assert store.list_companion_outcomes() == []  # slice never reached


# VS-07 ---------------------------------------------------------------------


def test_vs07_bookkeeping_failure_conversation_remains_valid(tmp_path, monkeypatch):
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
    # No companion outcome row was left behind.
    assert store.get_companion_outcome(_turn_event_id(session.id, result.assistant.id)) is None


# VS-08 ---------------------------------------------------------------------


def test_vs08_outcome_contains_no_message_text(tmp_path):
    store, session, service = make_store_service(
        tmp_path, answer="This is the private assistant response content."
    )
    runtime = start_runtime(companion_enabled=True)
    result = runtime.run_conversation(service, session.id, "hello", persistence=store)

    row = store.get_companion_outcome(_turn_event_id(session.id, result.assistant.id))
    assert row is not None
    assert row[6] == result.assistant.id  # evidence = message id only
    assert "private assistant response" not in (row[5] or "")
    assert result.assistant.content not in (row[5] or "")


# VS-09 ---------------------------------------------------------------------


def test_vs09_denied_policy_not_recorded_as_successful(tmp_path):
    store, session, _service = make_store_service(tmp_path)
    now = datetime.now(timezone.utc)
    turn = persist_turn(store, session.id, "hello")

    result = record_completed_turn(
        store,
        session_id=session.id,
        turn=turn,
        occurred_at=now,
        conditions=PolicyConditions(permission_denied=True),
    )

    assert result.outcome.status is OutcomeStatus.REJECTED
    assert result.outcome.action_id is None
    assert result.outcome.evidence is None
    # State still records that the event was processed (bounded, evidence-based).
    assert result.state.revision == 1
    rows = store.list_companion_outcomes()
    assert len(rows) == 1
    assert rows[0][4] == OutcomeStatus.REJECTED.value


# VS-10 ---------------------------------------------------------------------


def test_vs10_state_claims_no_awareness_or_presence(tmp_path):
    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=True)
    runtime.run_conversation(service, session.id, "hello", persistence=store)

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

    with store._connect() as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(companion_state)")}
    expected = {
        "id",
        "revision",
        "last_event_id",
        "last_outcome_id",
        "last_observed_at",
        "conversation_active",
        "pending_presence_notices",
    }
    assert columns == expected


# VS-11 ---------------------------------------------------------------------


def test_vs11_outcome_and_state_survive_store_restart(tmp_path):
    db_path = tmp_path / "slice.db"
    store = SQLiteStore(db_path)
    session = store.create_session("slice-session")
    service = ConversationService(
        FakeProvider("hello"), store, ContextBuilder("system", "identity", 10000)
    )
    runtime = start_runtime(companion_enabled=True)
    result = runtime.run_conversation(service, session.id, "hello", persistence=store)

    event_id = _turn_event_id(session.id, result.assistant.id)
    assert store.get_companion_outcome(event_id) is not None
    assert store.get_companion_state() is not None

    # Reopen the same database file: outcome and state survive.
    store2 = SQLiteStore(db_path)
    assert store2.get_companion_outcome(event_id) is not None
    state_row = store2.get_companion_state()
    assert state_row is not None
    assert state_row[0] == 1  # revision survives


# VS-12 ---------------------------------------------------------------------


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


def test_vs12_transaction_rollback_leaves_no_partial_state(tmp_path, monkeypatch):
    store, session, _service = make_store_service(tmp_path)
    now = datetime.now(timezone.utc)
    turn = persist_turn(store, session.id, "hello")

    original_connect = store._connect

    def failing_connect():
        return _FailingConnection(original_connect())

    monkeypatch.setattr(store, "_connect", failing_connect)

    with pytest.raises(sqlite3.OperationalError, match="simulated disk failure"):
        record_completed_turn(
            store, session_id=session.id, turn=turn, occurred_at=now
        )

    # Atomic rollback: no outcome row, no state row, conversation untouched.
    assert store.get_companion_outcome(_turn_event_id(session.id, turn.assistant.id)) is None
    assert store.get_companion_state() is None
    messages = store.messages(session.id)
    assert len(messages) == 2  # conversation persistence is a separate transaction


# Config ---------------------------------------------------------------------


def test_companion_flag_defaults_off(tmp_path):
    config_file = tmp_path / "app.toml"
    config_file.write_text(
        '[sylphy]\ndata_dir="state"\n[ollama]\nmodel="local"\n[companion]\nenabled=false\n',
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert config.companion_enabled is False

    config_file.write_text(
        '[sylphy]\ndata_dir="state"\n[ollama]\nmodel="local"\n[companion]\nenabled=true\n',
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert config.companion_enabled is True


def test_companion_disabled_runtime_produces_no_outcome(tmp_path):
    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=False)
    runtime.run_conversation(service, session.id, "hello", persistence=store)
    assert store.list_companion_outcomes() == []


# Contract import sanity -----------------------------------------------------


def test_slice_result_is_frozen_dataclass(tmp_path):
    store, session, _service = make_store_service(tmp_path)
    now = datetime.now(timezone.utc)
    turn = persist_turn(store, session.id, "hello")
    result = record_completed_turn(
        store, session_id=session.id, turn=turn, occurred_at=now
    )
    assert isinstance(result, SliceResult)
    assert isinstance(result.outcome.event_id, str)
    assert isinstance(result.state.revision, int)
    assert isinstance(result.skipped_duplicate, bool)


# RT-01 ---------------------------------------------------------------------


def test_rt01_enabled_exactly_one_outcome_state_revision_and_turn_ids(tmp_path):
    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=True)
    result = runtime.run_conversation(service, session.id, "hello", persistence=store)

    messages = store.messages(session.id)
    assert len(messages) == 2
    assert messages[1].id == result.assistant.id
    assert messages[1].content == result.assistant.content

    rows = store.list_companion_outcomes()
    assert len(rows) == 1
    assert rows[0][1] == _turn_event_id(session.id, result.assistant.id)
    assert rows[0][4] == OutcomeStatus.SUCCEEDED.value
    assert rows[0][3] is not None
    assert rows[0][6] == result.assistant.id
    assert result.assistant.content not in (rows[0][5] or "")

    state_row = store.get_companion_state()
    assert state_row is not None
    assert state_row[0] == 1
    assert state_row[1] == _turn_event_id(session.id, result.assistant.id)
    assert state_row[2] is not None


# RT-02 ---------------------------------------------------------------------


def test_rt02_disabled_persistence_succeeds_no_outcome_no_state(tmp_path):
    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=False)
    result = runtime.run_conversation(service, session.id, "hello", persistence=store)

    messages = store.messages(session.id)
    assert len(messages) == 2
    assert messages[1].id == result.assistant.id

    assert store.list_companion_outcomes() == []
    assert store.get_companion_state() is None


# RT-03 ---------------------------------------------------------------------


def test_rt03_provider_failure_no_outcome_state_unchanged(tmp_path):
    store, session, service = make_store_service(tmp_path, failure=RuntimeError("offline"))
    runtime = start_runtime(companion_enabled=True)

    with pytest.raises(RuntimeError, match="offline"):
        runtime.run_conversation(service, session.id, "hello", persistence=store)

    assert store.list_companion_outcomes() == []
    assert store.get_companion_state() is None
    assert store.messages(session.id) == []


# RT-04 ---------------------------------------------------------------------


def test_rt04_persistence_failure_no_bookkeeping(tmp_path, monkeypatch):
    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=True)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(store, "append_turn", boom)

    with pytest.raises(sqlite3.OperationalError, match="disk full"):
        runtime.run_conversation(service, session.id, "hello", persistence=store)

    assert store.list_companion_outcomes() == []
    assert store.get_companion_state() is None


# RT-05 ---------------------------------------------------------------------


def test_rt05_bookkeeping_failure_conversation_remains_valid(tmp_path, monkeypatch, caplog):
    store, session, service = make_store_service(tmp_path, answer="assistant reply")
    runtime = start_runtime(companion_enabled=True)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("bookkeeping down")

    monkeypatch.setattr(store, "record_companion_outcome", boom)

    with caplog.at_level(logging.DEBUG):
        result = runtime.run_conversation(service, session.id, "hello", persistence=store)

    messages = store.messages(session.id)
    assert len(messages) == 2
    assert messages[1].content == "assistant reply"

    assert result.assistant.content == "assistant reply"

    assert "Companion bookkeeping failed" in caplog.text

    assert store.get_companion_outcome(_turn_event_id(session.id, result.assistant.id)) is None
    assert store.get_companion_state() is None


# RT-06 ---------------------------------------------------------------------


def test_rt06_missing_companion_section_disabled(tmp_path):
    config_file = tmp_path / "app.toml"
    nl = chr(10)
    config_file.write_text(
        f'[sylphy]{nl}data_dir="state"{nl}[ollama]{nl}model="local"{nl}',
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert config.companion_enabled is False


def test_rt06_explicit_false_disabled(tmp_path):
    config_file = tmp_path / "app.toml"
    nl = chr(10)
    config_file.write_text(
        f'[sylphy]{nl}data_dir="state"{nl}[ollama]{nl}model="local"{nl}[companion]{nl}enabled=false{nl}',
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert config.companion_enabled is False


def test_rt06_explicit_true_enabled(tmp_path):
    config_file = tmp_path / "app.toml"
    nl = chr(10)
    config_file.write_text(
        f'[sylphy]{nl}data_dir="state"{nl}[ollama]{nl}model="local"{nl}[companion]{nl}enabled=true{nl}',
        encoding="utf-8",
    )
    config = load_config(config_file)
    assert config.companion_enabled is True


def test_rt06_invalid_value_rejected(tmp_path):
    config_file = tmp_path / "app.toml"
    nl = chr(10)
    config_file.write_text(
        f'[sylphy]{nl}data_dir="state"{nl}[ollama]{nl}model="local"{nl}[companion]{nl}enabled="yes"{nl}',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="companion.enabled must be a boolean"):
        load_config(config_file)


# RT-07 ---------------------------------------------------------------------


def test_rt07_multiple_turns_two_outcomes_progression(tmp_path):
    store, session, service = make_store_service(tmp_path)
    runtime = start_runtime(companion_enabled=True)

    result1 = runtime.run_conversation(service, session.id, "first", persistence=store)
    result2 = runtime.run_conversation(service, session.id, "second", persistence=store)

    messages = store.messages(session.id)
    assert len(messages) == 4

    rows = store.list_companion_outcomes()
    assert len(rows) == 2
    event_ids = {rows[0][1], rows[1][1]}
    assert event_ids == {
        _turn_event_id(session.id, result1.assistant.id),
        _turn_event_id(session.id, result2.assistant.id),
    }
    assert all(row[4] == OutcomeStatus.SUCCEEDED.value for row in rows)

    state_row = store.get_companion_state()
    assert state_row[0] == 2
    assert state_row[1] == _turn_event_id(session.id, result2.assistant.id)
