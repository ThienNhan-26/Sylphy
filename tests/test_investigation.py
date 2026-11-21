from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from sylphy.config import load_config
from sylphy.exploration import ExplorationQuestion
from sylphy.investigation import Investigation, InvestigationBudget, InvestigationStatus
from sylphy.runtime import Runtime, RuntimePhase
from sylphy.storage import SQLiteStore


def make_question(text="What should be investigated?", *, status="pending", expires=None):
    now = datetime.now(timezone.utc)
    created = expires - timedelta(days=1) if expires is not None and expires <= now else now
    return ExplorationQuestion(
        id="question-" + str(abs(hash(text))), question=text, source_session_id="session-test",
        source_message_ids=("user-1", "assistant-1"), source_memory_ids=(), created_at=created,
        status=status, attempt_count=0, expires_at=expires or now + timedelta(days=3),
    )


def persisted_question(store, **kwargs):
    question = make_question(**kwargs)
    return store.create_exploration_question(question, max_queue_items=20)


def test_domain_state_transitions_attempt_limit_and_expiry():
    now = datetime.now(timezone.utc)
    budget = InvestigationBudget(max_duration_seconds=60)
    item = Investigation.queued("q", "A question?", budget, max_attempts=1, now=now)
    assert item.status is InvestigationStatus.QUEUED
    assert item.expires_at == now + timedelta(seconds=60)

    running = item.transition(InvestigationStatus.RUNNING, now + timedelta(seconds=1))
    assert running.attempt_count == 1
    with pytest.raises(ValueError, match="attempt limit"):
        running.transition(InvestigationStatus.QUEUED).transition(InvestigationStatus.RUNNING)
    failed = running.transition(InvestigationStatus.FAILED, error="inference failed")
    assert failed.last_error == "inference failed"
    with pytest.raises(ValueError, match="Invalid investigation transition"):
        failed.transition(InvestigationStatus.RUNNING)
    with pytest.raises(ValueError, match="expired"):
        item.transition(InvestigationStatus.RUNNING, now + timedelta(seconds=60))


@pytest.mark.parametrize("field,value", [
    ("max_duration_seconds", 0), ("max_model_calls", 101), ("max_search_requests", True),
    ("max_pages", -1), ("max_source_bytes", 50_000_001), ("max_extracted_text_chars", 0),
])
def test_budget_rejects_nonpositive_or_unbounded_values(field, value):
    values = InvestigationBudget().as_dict()
    values[field] = value
    with pytest.raises(ValueError):
        InvestigationBudget(**values)


def test_storage_create_deduplicate_restart_and_transition(tmp_path):
    path = tmp_path / "investigations.sqlite3"
    store = SQLiteStore(path)
    question = persisted_question(store)
    budget = InvestigationBudget(max_duration_seconds=321, max_search_requests=2)
    created = store.create_investigation(question.id, budget, max_attempts=2)
    duplicate = store.create_investigation(question.id, InvestigationBudget(), max_attempts=3)
    assert duplicate == created
    assert created.source_question_id == question.id
    assert created.question == question.question
    assert created.status is InvestigationStatus.QUEUED
    assert created.budget == budget
    assert created.max_attempts == 2
    assert store.get_investigation(created.id) == created
    assert store.get_investigation_for_question(question.id) == created
    assert store.list_investigations() == [created]

    reopened = SQLiteStore(path)
    assert reopened.get_investigation(created.id) == created
    running = reopened.transition_investigation(created.id, InvestigationStatus.RUNNING)
    assert running.attempt_count == 1
    cancelled = reopened.cancel_investigation(created.id)
    assert cancelled.status is InvestigationStatus.CANCELLED
    with pytest.raises(ValueError, match="Invalid investigation transition"):
        reopened.transition_investigation(created.id, InvestigationStatus.RUNNING)


def test_storage_rejects_invalid_question_and_missing_ids(tmp_path):
    store = SQLiteStore(tmp_path / "eligibility.sqlite3")
    with pytest.raises(KeyError, match="not found"):
        store.create_investigation("missing", InvestigationBudget())
    dismissed = persisted_question(store, status="dismissed")
    with pytest.raises(ValueError, match="dismissed"):
        store.create_investigation(dismissed.id, InvestigationBudget())

    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired = persisted_question(store, text="Already expired?", expires=expired_at)
    store.list_exploration_questions(None)
    expired = store.get_exploration_question(expired.id)
    assert expired.status == "expired"
    with pytest.raises(ValueError, match="expired"):
        store.create_investigation(expired.id, InvestigationBudget())
    assert store.get_investigation("missing") is None


def test_investigation_insert_rolls_back_on_sql_failure(tmp_path):
    path = tmp_path / "rollback.sqlite3"
    store = SQLiteStore(path)
    question = persisted_question(store)
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TRIGGER reject_investigation BEFORE INSERT ON investigations
                     BEGIN SELECT RAISE(ABORT, 'simulated failure'); END""")
    with pytest.raises(sqlite3.IntegrityError, match="simulated failure"):
        store.create_investigation(question.id, InvestigationBudget())
    assert store.get_investigation_for_question(question.id) is None


def test_additive_migration_preserves_questions_and_creates_investigation_schema(tmp_path):
    path = tmp_path / "pre_investigation.sqlite3"
    old_store = SQLiteStore(path)
    question = persisted_question(old_store)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE investigations")

    migrated = SQLiteStore(path)
    assert migrated.get_exploration_question(question.id) == question
    created = migrated.create_investigation(question.id, InvestigationBudget())
    with sqlite3.connect(path) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(investigations)")}
        indexes = {row[1] for row in db.execute("PRAGMA index_list(investigations)")}
    assert {"source_question_id", "status", "attempt_count", "max_model_calls", "max_pages"} <= columns
    assert "idx_investigations_status_created" in indexes
    assert "idx_investigations_expiry" in indexes
    assert migrated.get_investigation(created.id) == created


def test_runtime_recovers_running_and_defers_on_shutdown_without_worker(tmp_path):
    store = SQLiteStore(tmp_path / "runtime.sqlite3")
    question = persisted_question(store)
    item = store.create_investigation(question.id, InvestigationBudget())
    store.transition_investigation(item.id, InvestigationStatus.RUNNING)

    runtime = Runtime("unused-model", heartbeat_enabled=False)
    runtime.configure_investigations(store, InvestigationBudget())
    runtime.start()
    assert runtime.state.snapshot().phase is RuntimePhase.READY
    recovered = store.get_investigation(item.id)
    assert recovered.status is InvestigationStatus.QUEUED
    assert recovered.attempt_count == 1
    assert recovered.deferred_at is not None
    assert runtime.cognition_worker is None
    assert runtime.exploration_worker is None
    assert runtime.resources._PRIORITY == {
        "conversation": 0, "vision": 1, "cognition": 2, "exploration": 3, "investigation": 4,
    }

    store.transition_investigation(item.id, InvestigationStatus.RUNNING)
    runtime.shutdown()
    assert runtime.state.snapshot().phase is RuntimePhase.STOPPED
    assert store.get_investigation(item.id).status is InvestigationStatus.QUEUED


def test_recovery_expires_deadline_and_fails_exhausted_attempt(tmp_path):
    store = SQLiteStore(tmp_path / "recovery.sqlite3")
    expired_question = persisted_question(store, text="Deadline?", expires=datetime.now(timezone.utc) + timedelta(days=3))
    expired = store.create_investigation(expired_question.id, InvestigationBudget())
    with sqlite3.connect(tmp_path / "recovery.sqlite3") as db:
        created = datetime.now(timezone.utc) - timedelta(days=2)
        deadline = created + timedelta(days=1)
        db.execute("UPDATE investigations SET created_at=?,updated_at=?,expires_at=? WHERE id=?",
                   (created.isoformat(), created.isoformat(), deadline.isoformat(), expired.id))

    exhausted_question = persisted_question(store, text="Attempt limit?")
    exhausted = store.create_investigation(exhausted_question.id, InvestigationBudget(), max_attempts=1)
    store.transition_investigation(exhausted.id, InvestigationStatus.RUNNING)

    runtime = Runtime("unused-model", heartbeat_enabled=False)
    runtime.configure_investigations(store, InvestigationBudget())
    runtime.start()
    assert store.get_investigation(expired.id).status is InvestigationStatus.EXPIRED
    failed = store.get_investigation(exhausted.id)
    assert failed.status is InvestigationStatus.FAILED
    assert failed.last_error
    runtime.shutdown()


def test_runtime_investigation_management_and_invalid_id(tmp_path):
    store = SQLiteStore(tmp_path / "runtime-api.sqlite3")
    question = persisted_question(store)
    runtime = Runtime("unused-model", heartbeat_enabled=False)
    with pytest.raises(RuntimeError, match="disabled"):
        runtime.queue_investigation(question.id)
    runtime.configure_investigations(store, InvestigationBudget(), max_attempts=2)
    item = runtime.queue_investigation(question.id)
    assert runtime.list_investigations() == [item]
    assert runtime.get_investigation(item.id) == item
    with pytest.raises(KeyError, match="not found"):
        runtime.cancel_investigation("missing")


def test_runtime_reports_shutdown_persistence_failure(tmp_path):
    class BrokenStore:
        def recover_investigations(self):
            return 0

        def defer_running_investigations(self):
            raise sqlite3.OperationalError("simulated persistence failure")

    runtime = Runtime("unused-model", heartbeat_enabled=False)
    runtime.configure_investigations(BrokenStore(), InvestigationBudget())
    runtime.start()
    with pytest.raises(sqlite3.OperationalError, match="simulated persistence failure"):
        runtime.shutdown()
    assert runtime.state.snapshot().phase is RuntimePhase.ERROR


@pytest.mark.parametrize("field,value", [
    ("max_attempts", 0), ("max_attempts", 11), ("max_pages", 101),
    ("max_duration_seconds", -4), ("max_source_bytes", 50_000_001),
])
def test_config_validates_investigation_limits(tmp_path, field, value):
    path = tmp_path / "config.toml"
    path.write_text(f"[investigation]\n{field} = {value}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="investigation"):
        load_config(path)


def test_config_defaults_disabled_and_accepts_optional_limits(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[sylphy]\ndata_dir='isolated'\n", encoding="utf-8")
    config = load_config(path)
    assert config.investigation_enabled is False
    assert config.investigation_max_attempts == 3
    assert config.investigation_max_search_requests == 3


def test_cli_investigation_commands(tmp_path, capsys):
    from sylphy.app import _investigation_command

    store = SQLiteStore(tmp_path / "cli.sqlite3")
    question = persisted_question(store)
    runtime = Runtime("unused-model", heartbeat_enabled=False)
    runtime.configure_investigations(store, InvestigationBudget())

    _investigation_command(f"/investigation start {question.id}", runtime)
    output = capsys.readouterr().out
    assert "Web Search, and Web Reader enabled" in output
    item = runtime.queue_investigation(question.id)

    _investigation_command("/investigation list", runtime)
    assert item.id in capsys.readouterr().out
    _investigation_command(f"/investigation inspect {item.id}", runtime)
    assert item.question in capsys.readouterr().out
    _investigation_command(f"/investigation cancel {item.id}", runtime)
    assert "cancelled" in capsys.readouterr().out
    _investigation_command("/investigation inspect invalid", runtime)
    assert "not found" in capsys.readouterr().out
