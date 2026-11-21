from datetime import datetime, timedelta, timezone
from threading import Event
import sqlite3

from sylphy.app import _deliver_presence, _prompt_user
from sylphy.core import Message, Turn
from sylphy.exploration import ExplorationQuestion
from sylphy.presence import PresenceEvent, PresenceEventType
from sylphy.runtime import Runtime
from sylphy.storage import SQLiteStore


def make_turn(store):
    session = store.create_session()
    now = datetime.now(timezone.utc)
    turn = Turn(
        session.id,
        Message("presence-user", session.id, "user", "I enjoy learning about gardens.", now),
        Message("presence-assistant", session.id, "assistant", "Gardens can be fascinating.", now),
    )
    store.append_turn(turn.user, turn.assistant)
    return turn


def make_question(turn, question="What makes gardens welcoming?"):
    now = datetime.now(timezone.utc)
    return ExplorationQuestion(
        "question-presence-1", question, turn.session_id,
        (turn.user.id, turn.assistant.id), (), now, "pending", 0,
        now + timedelta(days=3), topic="gardens", reason="The conversation raised the topic.",
    )


def configured_runtime(store, service, *, presence_enabled=True, capacity=20):
    runtime = Runtime("unused-model", heartbeat_enabled=False,
                      presence_enabled=presence_enabled, presence_queue_capacity=capacity)
    runtime.configure_exploration_worker(
        store, service, max_queue_items=10, cooldown_seconds=0.01,
        max_attempts_per_period=3, period_seconds=3600,
    )
    runtime.start()
    return runtime


def enqueue_and_wait(runtime, turn, store, status="completed"):
    task = runtime.exploration_worker.enqueue(turn)
    assert task is not None
    event = Event()
    # Poll the persisted task state without sleeping in the test thread.
    import time
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        current = store.get_exploration_task(task.id)
        if current.status == status:
            event.set()
            break
        event.wait(0.005)
    assert event.is_set(), f"Exploration task did not reach {status}"


class SavingService:
    def __init__(self, store):
        self.store = store

    def analyze(self, task, turn, messages, cancel=None):
        return self.store.create_exploration_question(make_question(turn), max_queue_items=10)


def test_saved_pending_question_publishes_one_stable_presence_event(tmp_path):
    store = SQLiteStore(tmp_path / "exploration-presence.db")
    turn = make_turn(store)
    runtime = configured_runtime(store, SavingService(store))
    try:
        enqueue_and_wait(runtime, turn, store)
        question = store.list_exploration_questions()[0]
        pending = runtime.presence.pending()
        assert len(pending) == 1
        assert pending[0].title == "A question to explore"
        assert pending[0].content == question.question
        assert pending[0].source_id == "exploration"
        assert pending[0].deduplication_key == question.id
        assert question.status == "pending"
        assert runtime._publish_exploration_question(question) is False
        assert runtime.presence.pending() == pending
    finally:
        runtime.shutdown()


def test_presence_disabled_does_not_publish_after_question_is_saved(tmp_path):
    store = SQLiteStore(tmp_path / "disabled.db")
    turn = make_turn(store)
    runtime = configured_runtime(store, SavingService(store), presence_enabled=False)
    try:
        enqueue_and_wait(runtime, turn, store)
        saved = store.list_exploration_questions()[0]
        assert saved.status == "pending"
        assert saved.notification_status is None
        assert runtime.presence.pending() == ()
    finally:
        runtime.shutdown()


def test_persistence_failure_never_publishes_presence(tmp_path, monkeypatch):
    store = SQLiteStore(tmp_path / "persistence-failure.db")
    turn = make_turn(store)
    def fail_to_save(*_args, **_kwargs):
        raise sqlite3.OperationalError("simulated question persistence failure")
    monkeypatch.setattr(store, "create_exploration_question", fail_to_save)
    runtime = configured_runtime(store, SavingService(store))
    try:
        enqueue_and_wait(runtime, turn, store, status="failed")
        assert store.list_exploration_questions() == []
        assert runtime.presence.pending() == ()
    finally:
        runtime.shutdown()


def test_dismissed_or_expired_question_is_not_eligible_for_presence(tmp_path):
    store = SQLiteStore(tmp_path / "ineligible.db")
    turn = make_turn(store)
    runtime = Runtime("unused-model", heartbeat_enabled=False, presence_enabled=True)
    runtime.configure_exploration_worker(store, SavingService(store), 10, 60, 3, 3600)
    runtime.start()
    try:
        question = store.create_exploration_question(make_question(turn), 10)
        store.dismiss_exploration_question(question.id)
        assert runtime._publish_exploration_question(question) is False
        now = datetime.now(timezone.utc)
        expired = ExplorationQuestion(
            "question-presence-expired", "Why did this old question expire?", turn.session_id,
            (turn.user.id, turn.assistant.id), (), now - timedelta(days=4), "pending", 0,
            now - timedelta(days=1), topic="old", reason=None,
        )
        expired = store.create_exploration_question(expired, 10)
        assert expired is not None
        assert runtime._publish_exploration_question(expired) is False
        assert runtime.presence.pending() == ()
    finally:
        runtime.shutdown()


def test_full_presence_queue_rejects_exploration_notice(tmp_path):
    store = SQLiteStore(tmp_path / "capacity.db")
    turn = make_turn(store)
    runtime = Runtime("unused-model", heartbeat_enabled=False,
                      presence_enabled=True, presence_queue_capacity=1)
    runtime.configure_exploration_worker(store, SavingService(store), 10, 60, 3, 3600)
    runtime.start()
    try:
        existing = PresenceEvent(PresenceEventType.NOTICE, "Existing", "Keep this notice.")
        assert runtime.publish_presence(existing)
        question = store.create_exploration_question(make_question(turn), 10)
        assert runtime._publish_exploration_question(question) is False
        assert runtime.presence.pending() == (existing,)
        assert store.get_exploration_question(question.id).notification_status is None
    finally:
        runtime.shutdown()


def test_dismissing_notice_removes_it(tmp_path):
    store = SQLiteStore(tmp_path / "prompt-boundary.db")
    turn = make_turn(store)
    runtime = Runtime("unused-model", heartbeat_enabled=False, presence_enabled=True)
    runtime.configure_exploration_worker(store, SavingService(store), 10, 60, 3, 3600)
    runtime.start()
    try:
        question = store.create_exploration_question(make_question(turn), 10)
        assert runtime._publish_exploration_question(question)
        item = runtime.presence.pending()[0]
        assert runtime.presence.dismiss(item.event_id)
        assert runtime.presence.pending() == ()
    finally:
        runtime.shutdown()


def test_tui_delivers_presence_only_at_prompt_boundary(tmp_path, monkeypatch, capsys):
    store = SQLiteStore(tmp_path / "prompt-boundary-tui.db")
    turn = make_turn(store)
    runtime = Runtime("unused-model", heartbeat_enabled=False, presence_enabled=True)
    runtime.configure_exploration_worker(store, SavingService(store), 10, 60, 3, 3600)
    runtime.start()
    try:
        question = store.create_exploration_question(make_question(turn), 10)
        assert runtime._publish_exploration_question(question)
        observed_before_input = []
        def fake_input(prompt):
            observed_before_input.append(capsys.readouterr().out)
            return "hello"
        monkeypatch.setattr("builtins.input", fake_input)
        assert _prompt_user(runtime) == "hello"
        assert "[SYLPHY PRESENCE" in observed_before_input[0]
        assert "A question to explore" in observed_before_input[0]
        assert runtime.presence.pending() == ()
    finally:
        runtime.shutdown()


def test_exploration_notice_dedup_and_queued_state_survive_restart(tmp_path, capsys):
    path = tmp_path / "durable-notice.db"
    store = SQLiteStore(path)
    turn = make_turn(store)
    question = store.create_exploration_question(make_question(turn), 10)
    first = Runtime("unused-model", heartbeat_enabled=False, presence_enabled=True)
    first.configure_exploration_worker(store, SavingService(store), 10, 60, 3, 3600)
    first.start()
    try:
        assert first._publish_exploration_question(question)
        assert store.get_exploration_question(question.id).notification_status == "queued"
        assert len(first.presence.drain_pending()) == 1
        assert not first._publish_exploration_question(question)
    finally:
        first.shutdown()

    reopened = SQLiteStore(path)
    second = Runtime("unused-model", heartbeat_enabled=False, presence_enabled=True)
    second.configure_exploration_worker(reopened, SavingService(reopened), 10, 60, 3, 3600)
    second.start()
    try:
        assert len(second.presence.pending()) == 1
        assert not second._publish_exploration_question(question)
        _deliver_presence(second)
        assert "A question to explore" in capsys.readouterr().out
        assert reopened.get_exploration_question(question.id).notification_status == "delivered"
    finally:
        second.shutdown()

    third = Runtime("unused-model", heartbeat_enabled=False, presence_enabled=True)
    third.configure_exploration_worker(reopened, SavingService(reopened), 10, 60, 3, 3600)
    third.start()
    try:
        assert third.presence.pending() == ()
        assert not third._publish_exploration_question(question)
    finally:
        third.shutdown()


def test_presence_dismissal_is_durable_but_keeps_exploration_question(tmp_path):
    path = tmp_path / "dismiss-notice.db"
    store = SQLiteStore(path)
    turn = make_turn(store)
    question = store.create_exploration_question(make_question(turn), 10)
    runtime = Runtime("unused-model", heartbeat_enabled=False, presence_enabled=True)
    runtime.configure_exploration_worker(store, SavingService(store), 10, 60, 3, 3600)
    runtime.start()
    try:
        assert runtime._publish_exploration_question(question)
        event = runtime.presence.pending()[0]
        assert runtime.dismiss_presence(event.event_id)
        saved = store.get_exploration_question(question.id)
        assert saved.status == "pending"
        assert saved.notification_status == "dismissed"
        assert runtime.presence.pending() == ()
        assert not runtime._publish_exploration_question(saved)
    finally:
        runtime.shutdown()

    restarted = SQLiteStore(path)
    runtime2 = Runtime("unused-model", heartbeat_enabled=False, presence_enabled=True)
    runtime2.configure_exploration_worker(restarted, SavingService(restarted), 10, 60, 3, 3600)
    runtime2.start()
    try:
        assert runtime2.presence.pending() == ()
        assert restarted.get_exploration_question(question.id).status == "pending"
        assert not runtime2._publish_exploration_question(question)
    finally:
        runtime2.shutdown()


def test_exploration_question_dismissal_suppresses_and_removes_notice(tmp_path):
    store = SQLiteStore(tmp_path / "dismiss-question.db")
    turn = make_turn(store)
    question = store.create_exploration_question(make_question(turn), 10)
    runtime = Runtime("unused-model", heartbeat_enabled=False, presence_enabled=True)
    runtime.configure_exploration_worker(store, SavingService(store), 10, 60, 3, 3600)
    runtime.start()
    try:
        assert runtime._publish_exploration_question(question)
        dismissed = runtime.dismiss_exploration_question(question.id)
        assert dismissed.status == "dismissed"
        assert dismissed.notification_status == "dismissed"
        assert runtime.presence.pending() == ()
        assert not runtime._publish_exploration_question(question)
    finally:
        runtime.shutdown()


def test_existing_exploration_question_schema_gets_additive_notification_migration(tmp_path):
    path = tmp_path / "old-exploration-schema.db"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE exploration_questions (
            id TEXT PRIMARY KEY, question TEXT NOT NULL, source_session_id TEXT NOT NULL,
            source_message_ids TEXT NOT NULL, source_memory_ids TEXT NOT NULL,
            created_at TEXT NOT NULL, status TEXT NOT NULL, attempt_count INTEGER NOT NULL DEFAULT 0,
            expires_at TEXT NOT NULL, topic TEXT, reason TEXT, question_key TEXT NOT NULL UNIQUE
        )""")
        now = datetime.now(timezone.utc)
        db.execute(
            "INSERT INTO exploration_questions VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("old-question", "A saved question?", "session-old", '["user-old", "assistant-old"]',
             "[]", now.isoformat(), "pending", 0, (now + timedelta(days=2)).isoformat(),
             "topic", None, "old-key"),
        )
    migrated = SQLiteStore(path)
    question = migrated.get_exploration_question("old-question")
    assert question is not None
    assert question.id == "old-question" and question.status == "pending"
    assert question.source_message_ids == ("user-old", "assistant-old")
    assert question.notification_status is None
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(exploration_questions)")}
    assert "notification_status" in columns


def test_invalid_source_turn_cannot_create_presence_notice(tmp_path):
    store = SQLiteStore(tmp_path / "invalid-source.db")
    turn = make_turn(store)
    question = make_question(turn)
    invalid = ExplorationQuestion(
        question.id, question.question, question.source_session_id,
        ("missing-user", question.source_message_ids[1]), question.source_memory_ids,
        question.created_at, question.status, question.attempt_count, question.expires_at,
        question.topic, question.reason,
    )
    saved = store.create_exploration_question(invalid, 10)
    runtime = Runtime("unused-model", heartbeat_enabled=False, presence_enabled=True)
    runtime.configure_exploration_worker(store, SavingService(store), 10, 60, 3, 3600)
    runtime.start()
    try:
        assert saved is not None
        assert not runtime._publish_exploration_question(saved)
        assert runtime.presence.pending() == ()
        assert store.get_exploration_question(saved.id).notification_status is None
    finally:
        runtime.shutdown()
