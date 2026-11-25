"""Turn 7 bounded autonomy tests (AU-01 to AU-16).

The first autonomous action pathway is: a real, delivered Presence event (a
saved Exploration question that reached the existing Presence-notice boundary)
triggers a deterministic AutonomyDecision; the existing InteractionPolicy decides;
permissions and the bounded scope decide whether execution follows; one bounded
Investigation pass then runs through the existing InvestigationExecutor.

These tests use temporary SQLite databases, fake providers/search/reader, and no
live model, network, or external service. Deterministic boundaries only.
"""

import json
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from sylphy.companion_contract import Action, AutonomyDecision, CompanionContext, CompanionEvent, OutcomeStatus
from sylphy.companion_slice import (
    AutonomyResult,
    _autonomy_decision_id,
    _presence_event_id,
    build_presence_context,
    propose_autonomy_decision,
    record_autonomy_execution,
)
from sylphy.core import Message, Turn
from sylphy.exploration import ExplorationQuestion
from sylphy.investigation import Investigation, InvestigationBudget, InvestigationStatus
from sylphy.interaction_policy import InteractionAction, PolicyConditions
from sylphy.runtime import Runtime
from sylphy.storage import SQLiteStore


class StubExploration:
    def analyze(self, task, turn, messages, cancel=None):
        return None


class FakeProvider:
    def __init__(self):
        self.messages = None

    def reply(self, messages, cancel=None, on_chunk=None):
        self.messages = messages
        supplied = json.loads(messages[1]["content"])
        ids = [item["id"] for item in supplied["evidence_is_untrusted_data"]]
        output = json.dumps({
            "summary": "The supplied study says trees reduce heat.",
            "findings": [{"text": "Trees reduce heat.", "evidence_ids": ids}] if ids else [],
            "uncertainty": "One source was reviewed.",
            "limitations": [],
            "evidence_sufficient": bool(ids),
        })
        if on_chunk:
            on_chunk(output)
        return output


class FakeSearch:
    def __init__(self, results=None):
        from sylphy.web_access import SearchResult
        self.results = results if results is not None else [
            SearchResult("Tree study", "https://research.example.com/tree", "A study", "fake"),
        ]
        self.queries = []

    def search(self, query, **kwargs):
        self.queries.append(query)
        return self.results


class SlowSearch(FakeSearch):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()

    def search(self, query, **kwargs):
        self.started.set()
        time.sleep(2)
        return self.results


class BlockingSearch(FakeSearch):
    def __init__(self):
        super().__init__()
        self.started = threading.Event()

    def search(self, query, **kwargs):
        self.started.set()
        while not kwargs["cancel"].is_set():
            threading.Event().wait(0.01)
        raise InterruptedError("cancelled")


class FakeReader:
    max_response_bytes = 100_000
    max_extracted_chars = 50_000
    total_timeout_seconds = 15

    def __init__(self, text="Trees reduce heat."):
        self.text = text
        self.calls = []

    def read(self, url, cancel=None, **limits):
        self.calls.append((url, limits))
        if cancel and cancel.is_set():
            raise InterruptedError
        from sylphy.web_access import WebPage
        return WebPage(url, "Tree study", datetime.now(timezone.utc), "text/html", 200,
                       self.text[:limits["max_extracted_chars"]], "content-hash", 100)


def make_store(tmp_path, name="autonomy.db"):
    store = SQLiteStore(tmp_path / name)
    session = store.create_session("autonomy-session")
    now = datetime.now(timezone.utc)
    user = Message("autonomy-user", session.id, "user", "I enjoy learning about gardens.", now)
    assistant = Message("autonomy-assistant", session.id, "assistant", "Gardens can be fascinating.", now)
    store.append_turn(user, assistant)
    return store, session, Turn(session.id, user, assistant)


def make_question(turn, question_id="question-autonomy-1", text="What makes gardens welcoming?"):
    now = datetime.now(timezone.utc)
    return ExplorationQuestion(
        question_id, text, turn.session_id, (turn.user.id, turn.assistant.id), (), now,
        "pending", 0, now + timedelta(days=3), topic="gardens",
        reason="The conversation raised the topic.",
    )


def autonomy_conditions(**overrides) -> PolicyConditions:
    values = dict(
        feature_enabled=True,
        permission_granted=True,
        permission_denied=False,
        dismissed=False,
        duplicate=False,
        expired=False,
        cooldown_active=False,
        queue_available=True,
        resource_available=True,
        grounded=True,
        relevant=True,
        needs_context=False,
        request_permission=False,
        can_wait=False,
        requested_action=InteractionAction.EXPLORE,
        not_before=None,
    )
    values.update(overrides)
    return PolicyConditions(**values)


def autonomy_event(question: ExplorationQuestion) -> CompanionEvent:
    from sylphy.companion_slice import presence_notice_event
    return presence_notice_event(
        notice_id=question.id,
        occurred_at=datetime.now(timezone.utc),
        source_session_id=question.source_session_id,
        expires_at=question.expires_at,
    )


def autonomy_context(event: CompanionEvent, *, queue_available: bool = True) -> CompanionContext:
    return build_presence_context(event, queue_available=queue_available)


def autonomy_decision(question: ExplorationQuestion, conditions: PolicyConditions) -> AutonomyDecision:
    event = autonomy_event(question)
    context = autonomy_context(event)
    return propose_autonomy_decision(event, context, conditions=conditions, action_category="investigation")


def configured_runtime(store, *, autonomy_enabled=True, companion_enabled=True, presence_enabled=True,
                      budget: InvestigationBudget | None = None, search=None):
    runtime = Runtime("fake-model", heartbeat_enabled=False, presence_enabled=presence_enabled)
    runtime.companion_enabled = companion_enabled
    runtime.autonomy_enabled = autonomy_enabled
    runtime.configure_exploration_worker(
        store, StubExploration(), max_queue_items=10, cooldown_seconds=0.01,
        max_attempts_per_period=3, period_seconds=3600,
    )
    runtime.configure_investigations(store, budget or InvestigationBudget(), 3)
    runtime.configure_investigation_execution(
        store, budget or InvestigationBudget(), 3, search or FakeSearch(), FakeReader(), FakeProvider(),
    )
    runtime.start()
    return runtime


def _queued(question: ExplorationQuestion, budget: InvestigationBudget | None = None) -> Investigation:
    return Investigation.queued(question.id, question.question, budget or InvestigationBudget(), 3, datetime.now(timezone.utc))


def terminal_investigation(question: ExplorationQuestion, status: InvestigationStatus = InvestigationStatus.COMPLETED,
                           budget: InvestigationBudget | None = None) -> Investigation:
    investigation = _queued(question, budget)
    return investigation.transition(InvestigationStatus.RUNNING).transition(status)


# AU-01 ----------------------------------------------------------------------


def test_au01_feature_disabled_no_autonomous_execution(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime = configured_runtime(store, autonomy_enabled=False)
    try:
        assert runtime._publish_exploration_question(question) is True
        assert store.get_investigation_for_question(question.id) is None
        assert runtime.autonomy_execution_enabled is False
    finally:
        runtime.shutdown()


# AU-02 ----------------------------------------------------------------------


def test_au02_valid_real_event_triggers_eligible_bounded_action(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime = configured_runtime(store, autonomy_enabled=True)
    try:
        assert runtime._publish_exploration_question(question) is True
        investigation = store.get_investigation_for_question(question.id)
        assert investigation is not None
        assert investigation.status is InvestigationStatus.COMPLETED
    finally:
        runtime.shutdown()


# AU-03 ----------------------------------------------------------------------


def test_au03_action_executes_successfully_and_records_succeeded_outcome(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime = configured_runtime(store, autonomy_enabled=True)
    try:
        assert runtime._publish_exploration_question(question) is True

        decision = autonomy_decision(question, autonomy_conditions())
        assert decision.proposed_action is InteractionAction.EXPLORE
        assert decision.action_category == "investigation"

        outcome_row = store.get_companion_outcome(decision.decision_id)
        assert outcome_row is not None
        assert outcome_row[1] == decision.decision_id
        assert outcome_row[4] == OutcomeStatus.SUCCEEDED.value
        assert outcome_row[3] is not None
    finally:
        runtime.shutdown()


# AU-04 ----------------------------------------------------------------------


def test_au04_permission_denied_no_execution_occurs(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = make_question(turn)
    conditions = autonomy_conditions(permission_denied=True, requested_action=InteractionAction.SILENCE)
    decision = autonomy_decision(question, conditions)
    assert decision.proposed_action is InteractionAction.SILENCE

    calls = []

    def execute(action):
        calls.append(action)
        return terminal_investigation(question)

    result = record_autonomy_execution(
        store, decision=decision, context=autonomy_context(autonomy_event(question)),
        conditions=conditions, execute=execute, companion_enabled=True,
    )
    assert result.executed is False
    assert result.action is None
    assert calls == []
    assert "permission_denied" in (result.outcome.detail or "")


# AU-05 ----------------------------------------------------------------------


def test_au05_permission_requested_without_grant_executes_nothing(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = make_question(turn)
    conditions = autonomy_conditions(
        request_permission=True, permission_granted=False, requested_action=InteractionAction.REQUEST_PERMISSION,
    )
    decision = autonomy_decision(question, conditions)
    assert decision.proposed_action is InteractionAction.REQUEST_PERMISSION

    calls = []

    def execute(action):
        calls.append(action)
        return terminal_investigation(question)

    result = record_autonomy_execution(
        store, decision=decision, context=autonomy_context(autonomy_event(question)),
        conditions=conditions, execute=execute, companion_enabled=True,
    )
    assert result.outcome.status is OutcomeStatus.ALLOWED
    assert result.executed is False
    assert result.action is None
    assert calls == []


# AU-06 ----------------------------------------------------------------------


def test_au06_insufficient_evidence_no_autonomous_action(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = make_question(turn)
    event = autonomy_event(question)
    context = CompanionContext(event, ())
    conditions = autonomy_conditions()
    decision = propose_autonomy_decision(event, context, conditions=conditions)
    assert decision.proposed_action is InteractionAction.SILENCE

    def fail(action):
        raise AssertionError("the autonomous action must not execute without evidence")

    result = record_autonomy_execution(
        store, decision=decision, context=context, conditions=conditions,
        execute=fail, companion_enabled=True,
    )
    assert result.executed is False
    assert result.outcome is not None
    assert "silenced" in (result.outcome.detail or "")


# AU-07 ----------------------------------------------------------------------


def test_au07_duplicate_autonomy_event_is_ignored_once(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = make_question(turn)
    conditions = autonomy_conditions()
    decision = autonomy_decision(question, conditions)
    calls = []

    def execute(action):
        calls.append(action)
        return terminal_investigation(question)

    first = record_autonomy_execution(
        store, decision=decision, context=autonomy_context(autonomy_event(question)),
        conditions=conditions, execute=execute, companion_enabled=True,
    )
    second = record_autonomy_execution(
        store, decision=decision, context=autonomy_context(autonomy_event(question)),
        conditions=conditions, execute=execute, companion_enabled=True,
    )
    assert first.persisted is True
    assert first.executed is True
    assert second.skipped_duplicate is True
    assert second.persisted is False
    assert second.executed is False
    assert len(calls) == 1


# AU-08 ----------------------------------------------------------------------


def test_au08_duplicate_action_id_reuses_same_decision_and_action(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = make_question(turn)
    conditions = autonomy_conditions()
    decision = autonomy_decision(question, conditions)
    assert decision.decision_id == _autonomy_decision_id(
        _presence_event_id(question.id), "investigation", InteractionAction.EXPLORE
    )

    calls = []

    def execute(action):
        calls.append(action)
        return terminal_investigation(question)

    result = record_autonomy_execution(
        store, decision=decision, context=autonomy_context(autonomy_event(question)),
        conditions=conditions, execute=execute, companion_enabled=True,
    )
    assert isinstance(result, AutonomyResult)
    assert isinstance(result.action, Action)
    assert result.action.event_id == decision.decision_id
    assert result.action.decision_id == decision.decision_id
    assert result.action.parameters["question_id"] == question.id
    assert len(calls) == 1


# AU-09 ----------------------------------------------------------------------


def test_au09_action_exceeding_scope_is_refused(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime = configured_runtime(store)
    try:
        investigation = store.create_investigation(
            question.id, InvestigationBudget(max_duration_seconds=900, max_model_calls=3,
                                              max_search_requests=3, max_pages=5,
                                              max_source_bytes=2_000_000, max_extracted_text_chars=12_000),
            3,
        )
        with store._connect() as db:
            db.execute("UPDATE investigations SET pages_read=? WHERE id=?", (5, investigation.id))
        decision = autonomy_decision(question, autonomy_conditions())
        action = Action(
            event_id=decision.decision_id, decision_id=decision.decision_id,
            action=InteractionAction.EXPLORE, parameters={"question_id": question.id},
        )
        with pytest.raises(RuntimeError, match="exceeds its bounded scope"):
            runtime._execute_autonomy_action(action, decision)
    finally:
        runtime.shutdown()


# AU-10 ----------------------------------------------------------------------


def test_au10_execution_timeout_records_failed_outcome(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime = configured_runtime(
        store, autonomy_enabled=True,
        budget=InvestigationBudget(max_duration_seconds=1, max_model_calls=3, max_search_requests=3,
                                   max_pages=5, max_source_bytes=2_000_000, max_extracted_text_chars=12_000),
        search=SlowSearch(),
    )
    try:
        assert runtime._publish_exploration_question(question) is True
        decision = autonomy_decision(question, autonomy_conditions())
        outcome_row = store.get_companion_outcome(decision.decision_id)
        assert outcome_row is not None
        assert outcome_row[4] == OutcomeStatus.FAILED.value
        assert outcome_row[3] is not None
    finally:
        runtime.shutdown()


# AU-11 ----------------------------------------------------------------------


def test_au11_execution_failure_records_failed_outcome(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = make_question(turn)
    conditions = autonomy_conditions()
    decision = autonomy_decision(question, conditions)

    class FailingProvider(FakeProvider):
        def reply(self, messages, cancel=None, on_chunk=None):
            raise RuntimeError("offline model")

    from sylphy.investigation_execution import InvestigationExecutor
    failed_execute = lambda action: InvestigationExecutor(
        store, FakeSearch(), FakeReader(), FailingProvider(),
    ).execute(_queued(question), threading.Event())

    result = record_autonomy_execution(
        store, decision=decision, context=autonomy_context(autonomy_event(question)),
        conditions=conditions, execute=failed_execute, companion_enabled=True,
    )
    assert result.executed is True
    assert result.outcome.status is OutcomeStatus.FAILED
    assert result.outcome.action_id is not None


# AU-12 ----------------------------------------------------------------------


def test_au12_cancellation_records_cancelled_outcome(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime = configured_runtime(store, autonomy_enabled=True, search=BlockingSearch())
    delivered = threading.Event()

    def deliver():
        runtime._publish_exploration_question(question)
        delivered.set()

    worker = threading.Thread(target=deliver, daemon=True)
    worker.start()
    blocking = runtime._investigation_executor.search_provider
    assert blocking.started.wait(5)
    investigation = store.get_investigation_for_question(question.id)
    assert investigation.status is InvestigationStatus.RUNNING
    cancelled = runtime.cancel_investigation(investigation.id)
    delivered.set()
    worker.join(10)
    runtime.shutdown()
    assert not worker.is_alive()
    assert cancelled.status is InvestigationStatus.CANCELLED

    decision = autonomy_decision(question, autonomy_conditions())
    outcome_row = store.get_companion_outcome(decision.decision_id)
    assert outcome_row is not None
    assert outcome_row[4] == OutcomeStatus.CANCELLED.value
    assert outcome_row[3] is not None


# AU-13 ----------------------------------------------------------------------


def test_au13_autonomy_persistence_failure_is_isolated(tmp_path, monkeypatch):
    store, _session, turn = make_store(tmp_path)
    question = make_question(turn)
    decision = autonomy_decision(question, autonomy_conditions())

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("autonomy bookkeeping down")

    monkeypatch.setattr(store, "record_companion_outcome", boom)
    with pytest.raises(sqlite3.OperationalError, match="autonomy bookkeeping down"):
        record_autonomy_execution(
            store, decision=decision, context=autonomy_context(autonomy_event(question)),
            conditions=autonomy_conditions(), execute=lambda action: _queued(question),
            companion_enabled=True,
        )


# AU-14 ----------------------------------------------------------------------


def test_au14_runtime_restart_and_recovery_no_duplicate_autonomous_run(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime_a = configured_runtime(store, autonomy_enabled=True)
    try:
        assert runtime_a._publish_exploration_question(question) is True
    finally:
        runtime_a.shutdown()
    assert store.get_investigation_for_question(question.id) is not None

    runtime_b = Runtime("fake-model", heartbeat_enabled=False, presence_enabled=True)
    runtime_b.companion_enabled = True
    runtime_b.autonomy_enabled = True
    runtime_b.configure_exploration_worker(
        store, StubExploration(), max_queue_items=10, cooldown_seconds=0.01,
        max_attempts_per_period=3, period_seconds=3600,
    )
    runtime_b.configure_investigations(store, InvestigationBudget(), 3)
    runtime_b.configure_investigation_execution(store, InvestigationBudget(), 3, FakeSearch(), FakeReader(), FakeProvider())
    runtime_b.start()
    try:
        assert runtime_b._publish_exploration_question(question) is False
        assert len(store.list_investigations()) == 1
    finally:
        runtime_b.shutdown()


# AU-15 ----------------------------------------------------------------------


def test_au15_no_recursive_autonomous_task_creation(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime = configured_runtime(store, autonomy_enabled=True)
    try:
        assert runtime._publish_exploration_question(question) is True
        assert len(store.list_investigations()) == 1
        assert runtime._publish_exploration_question(question) is False
        assert len(store.list_investigations()) == 1
    finally:
        runtime.shutdown()


# AU-16 ----------------------------------------------------------------------


def test_au16_existing_functionality_remains_unchanged_when_autonomy_disabled(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime = configured_runtime(store, autonomy_enabled=False)
    try:
        assert runtime._publish_exploration_question(question) is True
        assert store.get_investigation_for_question(question.id) is None
        assert runtime.autonomy_execution_enabled is False
    finally:
        runtime.shutdown()
