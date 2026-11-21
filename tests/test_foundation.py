from datetime import datetime, timezone
from threading import Event

import pytest

from sylphy.config import load_config
from sylphy.context import ContextBuilder, ContextOverflowError
from sylphy.core import ConversationCancelled, ConversationService, Message
from sylphy.identity import load_identity
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


def make_service(path, answer="hello", history_limit=20, failure=None):
    store = SQLiteStore(path)
    return store, ConversationService(FakeProvider(answer, failure), store, ContextBuilder("system", "identity placeholder", 10000))


def test_config_loads_toml_and_environment_override(tmp_path, monkeypatch):
    config_file = tmp_path / "app.toml"
    config_file.write_text('[sylphy]\ndata_dir="state"\n[ollama]\nmodel="local"\n', encoding="utf-8")
    monkeypatch.setenv("OLLAMA_MODEL", "override")
    monkeypatch.setenv("SYLPHY_CONTEXT_BUDGET", "4096")
    monkeypatch.setenv("SYLPHY_IDENTITY_FILE", "custom.toml")
    config = load_config(config_file)
    assert str(config.data_dir) == "state"
    assert config.ollama_model == "override"
    assert config.context_budget == 4096
    assert str(config.identity_file) == "custom.toml"
    assert config.presence_enabled is False
    assert config.presence_queue_capacity == 20


def test_presence_configuration_defaults_disabled_and_validates_capacity(tmp_path):
    config_file = tmp_path / "presence.toml"
    config_file.write_text('[presence]\nenabled=true\nqueue_capacity=3\n', encoding="utf-8")
    config = load_config(config_file)
    assert config.presence_enabled is True
    assert config.presence_queue_capacity == 3
    config_file.write_text('[presence]\nenabled="yes"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="presence.enabled"):
        load_config(config_file)
    config_file.write_text('[presence]\nqueue_capacity=0\n', encoding="utf-8")
    with pytest.raises(ValueError, match="presence.queue_capacity"):
        load_config(config_file)


def test_identity_loads_and_invalid_or_missing_identity_fails(tmp_path):
    identity_file = tmp_path / "identity.toml"
    identity_file.write_text(
        '[identity]\nname="SYLPHY"\nidentity="local system"\npersonality="open"\n'
        'communication_style="conversational"\nrelationship="develops through interaction"\n'
        'boundaries="do not fabricate"\nversion="0.1"\n', encoding="utf-8")
    identity = load_identity(identity_file)
    assert identity.name == "SYLPHY" and identity.version == "0.1"
    assert "Name: SYLPHY" in identity.to_prompt()
    with pytest.raises(FileNotFoundError):
        load_identity(tmp_path / "missing.toml")
    identity_file.write_text('[identity]\nname=""\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Missing or empty"):
        load_identity(identity_file)


def test_runtime_configuration_rejects_invalid_limits(tmp_path):
    from sylphy.config import load_config
    path = tmp_path / "invalid.toml"
    path.write_text('[runtime]\nheartbeat_interval_seconds=0\n', encoding="utf-8")
    with pytest.raises(ValueError, match="heartbeat_interval_seconds"):
        load_config(path)
    path.write_text('[resources]\nmax_concurrent_generations=2\n', encoding="utf-8")
    with pytest.raises(ValueError, match="must be 1"):
        load_config(path)


def test_session_crud_and_message_order_survive_store_restart(tmp_path):
    path = tmp_path / "nested" / "history.db"
    store = SQLiteStore(path)
    one = store.create_session("first")
    two = store.create_session()
    assert {s.id for s in store.list_sessions()} == {one.id, two.id}
    assert store.get_session(one.id) == one
    updated = store.rename_session(one.id, "renamed")
    assert updated.title == "renamed" and updated.updated_at >= one.updated_at
    service = ConversationService(FakeProvider("hello"), store, ContextBuilder("sys", "id", 10000))
    service.respond(one.id, "hi")
    ConversationService(FakeProvider("again"), store, ContextBuilder("sys", "id", 10)).respond(one.id, "next")
    restarted = SQLiteStore(path)
    messages = restarted.messages(one.id)
    assert [m.role for m in messages] == ["user", "assistant", "user", "assistant"]
    assert [m.content for m in messages] == ["hi", "hello", "next", "again"]
    assert all(m.id and m.session_id == one.id and m.completed for m in messages)
    restarted.delete_session(one.id)
    assert restarted.get_session(one.id) is None
    assert restarted.messages(one.id) == []


def test_context_has_instructions_and_bounded_history():
    now = datetime.now(timezone.utc)
    history = [Message(str(i), "s", "user", str(i), now) for i in range(4)]
    context = ContextBuilder("system rules", "identity placeholder", 12).build(history, "current")
    assert context == [
        {"role": "system", "content": "system rules"},
        {"role": "system", "content": "identity placeholder"},
        {"role": "user", "content": "2"},
        {"role": "user", "content": "3"},
        {"role": "user", "content": "current"},
    ]


def test_context_budget_excludes_oldest_history_predictably():
    now = datetime.now(timezone.utc)
    history = [Message(str(i), "s", "user", "old" if i == 0 else "new", now) for i in range(3)]
    # Fixed context + current input consumes 3 estimates; one recent message fits.
    builder = ContextBuilder("1234", "5678", 4)
    context = builder.build(history, "abcd")
    assert context[-2:] == [{"role": "user", "content": "new"}, {"role": "user", "content": "abcd"}]
    assert all(item["content"] != "old" for item in context)


def test_context_overflow_never_discards_current_message():
    builder = ContextBuilder("system", "identity", 3)
    with pytest.raises(ContextOverflowError, match="current message"):
        builder.build([], "a very long current message")


def test_model_failure_does_not_persist_partial_turn(tmp_path):
    store, service = make_service(tmp_path / "history.db", failure=RuntimeError("offline"))
    session = store.create_session()
    with pytest.raises(RuntimeError, match="offline"):
        service.respond(session.id, "question")
    assert store.messages(session.id) == []


def test_cancellation_does_not_persist_completed_turn(tmp_path):
    store, service = make_service(tmp_path / "history.db")
    session = store.create_session()
    cancel = Event()
    cancel.set()
    with pytest.raises(ConversationCancelled):
        service.respond(session.id, "question", cancel=cancel)
    assert store.messages(session.id) == []


def test_g0_turn_database_is_migrated(tmp_path):
    import sqlite3
    path = tmp_path / "g0.db"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE turns (id INTEGER PRIMARY KEY, conversation_id TEXT, user_text TEXT, assistant_text TEXT, created_at TEXT)")
        db.execute("INSERT INTO turns VALUES (1, 'legacy-id', 'hi', 'hello', '2026-01-01T00:00:00+00:00')")
    store = SQLiteStore(path)
    assert store.get_session("legacy-id") is not None
    assert [m.content for m in store.messages("legacy-id")] == ["hi", "hello"]
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT value FROM schema_meta WHERE key='g0_turns_migrated'").fetchone() == ("1",)
    reopened = SQLiteStore(path)
    assert [m.content for m in reopened.messages("legacy-id")] == ["hi", "hello"]


def test_context_skips_oversized_recent_message_and_keeps_older_fitting_history():
    now = datetime.now(timezone.utc)
    history = [
        Message("old", "s", "user", "a", now),
        Message("large", "s", "user", "x" * 20, now),
    ]
    builder = ContextBuilder("s", "i", 4)
    context = builder.build(history, "q")
    assert context == [
        {"role": "system", "content": "s"},
        {"role": "system", "content": "i"},
        {"role": "user", "content": "a"},
        {"role": "user", "content": "q"},
    ]
