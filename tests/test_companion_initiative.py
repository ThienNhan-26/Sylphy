"""Turn 8 purposeful-initiative tests (IN-01 to IN-16).

The first meaningful initiative pathway is: a real, durably completed bounded
Investigation (the kind the Turn 7 autonomy layer can start) enters the Companion
Runtime through a deterministic initiative trigger; the initiative decision
evaluates whether the completed result warrants one bounded user-facing response;
the existing InteractionPolicy decides whether delivery may happen; permission and
scope decide whether it does.

These tests use temporary SQLite databases, fake providers/search/reader, and no
live model, network, or external service. Deterministic boundaries only.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from sylphy.companion_contract import Action, CompanionContext, CompanionEvent, InitiativeAction, OutcomeStatus
from sylphy.companion_slice import (
    _initiative_decision_id,
    _initiative_interaction_action,
    _presence_event_id,
    build_initiative_context,
    build_presence_context,
    initiative_trigger_event,
    propose_initiative_decision,
    record_initiative,
)
from sylphy.core import Message, Turn
from sylphy.exploration import ExplorationQuestion
from sylphy.investigation import (
    Investigation,
    InvestigationBudget,
    InvestigationFinding,
    InvestigationResult,
    InvestigationStatus,
)
from sylphy.interaction_policy import InteractionAction, PolicyConditions
from sylphy.presence import PresenceEvent, PresenceEventType, PresenceService
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


def make_store(tmp_path, name="initiative.db"):
    store = SQLiteStore(tmp_path / name)
    session = store.create_session("initiative-session")
    now = datetime.now(timezone.utc)
    user = Message("initiative-user", session.id, "user", "I enjoy learning about gardens.", now)
    assistant = Message("initiative-assistant", session.id, "assistant", "Gardens can be fascinating.", now)
    store.append_turn(user, assistant)
    return store, session, Turn(session.id, user, assistant)


def make_question(turn, question_id="question-initiative-1", text="What makes gardens welcoming?"):
    now = datetime.now(timezone.utc)
    return ExplorationQuestion(
        question_id, text, turn.session_id, (turn.user.id, turn.assistant.id), (), now,
        "pending", 0, now + timedelta(days=3), topic="gardens",
        reason="The conversation raised the topic.",
    )


def initiative_conditions(**overrides) -> PolicyConditions:
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
        requested_action=InteractionAction.SPEAK,
        not_before=None,
    )
    values.update(overrides)
    return PolicyConditions(**values)


def initiative_event(investigation_id: str) -> CompanionEvent:
    return initiative_trigger_event(investigation_id, occurred_at=datetime.now(timezone.utc))


def initiative_context(event: CompanionEvent, investigation_id: str, result) -> CompanionContext:
    return build_initiative_context(event, investigation_id=investigation_id, result=result)


def initiative_decision(event: CompanionEvent, context: CompanionContext, conditions: PolicyConditions, result=None, response=None):
    return propose_initiative_decision(event, context, conditions=conditions, result=result, response=response)


def completed_result(investigation_id: str, summary: str = "The supplied study says trees reduce heat.") -> InvestigationResult:
    return InvestigationResult(
        investigation_id, summary, (InvestigationFinding("Trees reduce heat.", ("ev-1",)),),
        "One source was reviewed.", (), True, datetime.now(timezone.utc),
    )


def stored_completed_investigation(store: SQLiteStore, question: ExplorationQuestion) -> Investigation:
    investigation = store.create_investigation(question.id, InvestigationBudget(), 3)
    running = investigation.transition(InvestigationStatus.RUNNING)
    return running.transition(InvestigationStatus.COMPLETED)


# IN-01 ----------------------------------------------------------------------


def test_in01_feature_disabled_proposes_silence_and_records_outcome(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    investigation = stored_completed_investigation(store, question)
    result = completed_result(investigation.id)
    event = initiative_event(investigation.id)
    context = initiative_context(event, investigation.id, result)
    decision = initiative_decision(event, context, initiative_conditions(feature_enabled=False))

    assert decision.proposed_action is InitiativeAction.SILENCE

    delivered_flags = []

    def execute(action: Action) -> bool:
        delivered_flags.append(action)
        return True

    outcome_row = record_initiative(
        store, decision=decision, context=context, conditions=initiative_conditions(feature_enabled=False),
        execute=execute,
    )
    assert outcome_row.delivered is False
    assert delivered_flags == []
    assert outcome_row.outcome.status is OutcomeStatus.ALLOWED
    assert outcome_row.persisted is True


# IN-02 ----------------------------------------------------------------------


def test_in02_complete_relevant_result_proposes_communication_and_delivers(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    investigation = stored_completed_investigation(store, question)
    result = completed_result(investigation.id)
    event = initiative_event(investigation.id)
    context = initiative_context(event, investigation.id, result)
    conditions = initiative_conditions()
    decision = initiative_decision(event, context, conditions, result=result, response=result.summary)

    assert decision.proposed_action is InitiativeAction.COMMUNICATE
    assert decision.required_permissions == ("companion_deliver",)

    delivered_flags = []

    def execute(action: Action) -> bool:
        delivered_flags.append(action)
        return True

    outcome_row = record_initiative(store, decision=decision, context=context, conditions=conditions, execute=execute)
    assert outcome_row.delivered is True
    assert len(delivered_flags) == 1
    assert outcome_row.outcome.status is OutcomeStatus.SUCCEEDED
    assert outcome_row.action is not None
    assert outcome_row.persisted is True


# IN-03 ----------------------------------------------------------------------


def test_in03_incomplete_result_silences_initiative(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    investigation = stored_completed_investigation(store, question)
    event = initiative_event(investigation.id)
    context = initiative_context(event, investigation.id, None)
    decision = initiative_decision(event, context, initiative_conditions())

    assert decision.proposed_action is InitiativeAction.SILENCE

    outcome_row = record_initiative(
        store, decision=decision, context=context, conditions=initiative_conditions(),
        execute=lambda action: (_ for _ in ()).throw(AssertionError("must not deliver")),
    )
    assert outcome_row.delivered is False
    assert outcome_row.outcome.status is OutcomeStatus.ALLOWED
    assert "silenced" in (outcome_row.outcome.detail or "").lower()


# IN-04 ----------------------------------------------------------------------


def test_in04_insufficient_evidence_silences_initiative(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    investigation = stored_completed_investigation(store, question)
    result = completed_result(investigation.id)
    result = InvestigationResult(
        result.investigation_id, result.summary, result.findings, result.uncertainty,
        result.limitations, False, result.created_at,
    )
    event = initiative_event(investigation.id)
    context = initiative_context(event, investigation.id, result)
    decision = initiative_decision(event, context, initiative_conditions())

    assert decision.proposed_action is InitiativeAction.SILENCE

    outcome_row = record_initiative(
        store, decision=decision, context=context, conditions=initiative_conditions(),
        execute=lambda action: (_ for _ in ()).throw(AssertionError("must not deliver")),
    )
    assert outcome_row.delivered is False
    assert outcome_row.outcome.status is OutcomeStatus.ALLOWED


# IN-05 ----------------------------------------------------------------------


def test_in05_duplicate_initiative_trigger_is_ignored(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    investigation = stored_completed_investigation(store, question)
    result = completed_result(investigation.id)
    event = initiative_event(investigation.id)
    context = initiative_context(event, investigation.id, result)
    conditions = initiative_conditions()
    decision = initiative_decision(event, context, conditions, result=result, response=result.summary)

    delivered_flags = []

    def execute(action: Action) -> bool:
        delivered_flags.append(action)
        return True

    first = record_initiative(store, decision=decision, context=context, conditions=conditions, execute=execute)
    second = record_initiative(store, decision=decision, context=context, conditions=conditions, execute=execute)
    assert first.delivered is True
    assert first.persisted is True
    assert second.skipped_duplicate is True
    assert second.persisted is False
    assert second.delivered is False
    assert len(delivered_flags) == 1


# IN-06 ----------------------------------------------------------------------


def test_in06_duplicate_communication_is_not_redelivered(tmp_path):
    service = PresenceService(enabled=True, capacity=5)
    service.start()
    now = datetime.now(timezone.utc)
    event = PresenceEvent(
        PresenceEventType.NOTICE, "Research finding", "The supplied study says trees reduce heat.",
        event_id="investigation-1", created_at=now, source_id="initiative",
        deduplication_key="initiative-investigation-1",
    )
    assert service.publish(event) is True
    assert service.publish(event) is False
    assert len(service.pending()) == 1
    service.stop()


# IN-07 ----------------------------------------------------------------------


def test_in07_policy_denies_unpermitted_initiative_delivery(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    investigation = stored_completed_investigation(store, question)
    result = completed_result(investigation.id)
    event = initiative_event(investigation.id)
    context = initiative_context(event, investigation.id, result)
    conditions = initiative_conditions(relevant=False)
    decision = initiative_decision(event, context, conditions, result=result, response=result.summary)

    assert decision.proposed_action is InitiativeAction.COMMUNICATE

    delivered_flags = []

    def execute(action: Action) -> bool:
        delivered_flags.append(action)
        return True

    outcome_row = record_initiative(store, decision=decision, context=context, conditions=conditions, execute=execute)
    assert outcome_row.delivered is False
    assert outcome_row.action is None
    assert delivered_flags == []
    assert outcome_row.outcome.status is OutcomeStatus.REJECTED
    assert "not_relevant" in (outcome_row.outcome.detail or "").lower()


# IN-08 ----------------------------------------------------------------------


def test_in08_permission_required_no_delivery_performed(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    investigation = stored_completed_investigation(store, question)
    result = completed_result(investigation.id)
    event = initiative_event(investigation.id)
    context = initiative_context(event, investigation.id, result)
    conditions = initiative_conditions(permission_granted=False)
    decision = initiative_decision(event, context, conditions, result=result, response=result.summary)

    assert decision.proposed_action is InitiativeAction.REQUEST_PERMISSION

    delivered_flags = []

    def execute(action: Action) -> bool:
        delivered_flags.append(action)
        return True

    outcome_row = record_initiative(store, decision=decision, context=context, conditions=conditions, execute=execute)
    assert outcome_row.delivered is False
    assert outcome_row.action is None
    assert delivered_flags == []
    assert outcome_row.outcome.status is OutcomeStatus.ALLOWED


# IN-09 ----------------------------------------------------------------------


def test_in09_permission_denied_refuses_initiative(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    investigation = stored_completed_investigation(store, question)
    result = completed_result(investigation.id)
    event = initiative_event(investigation.id)
    context = initiative_context(event, investigation.id, result)
    conditions = initiative_conditions(permission_denied=True)
    decision = initiative_decision(event, context, conditions, result=result, response=result.summary)

    assert decision.proposed_action is InitiativeAction.REJECT

    delivered_flags = []

    def execute(action: Action) -> bool:
        delivered_flags.append(action)
        return True

    outcome_row = record_initiative(store, decision=decision, context=context, conditions=conditions, execute=execute)
    assert outcome_row.delivered is False
    assert outcome_row.action is None
    assert delivered_flags == []
    assert outcome_row.outcome.status is OutcomeStatus.REJECTED


# IN-10 ----------------------------------------------------------------------


def test_in10_wait_defers_delivery_without_retry(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    investigation = stored_completed_investigation(store, question)
    result = completed_result(investigation.id)
    event = initiative_event(investigation.id)
    context = initiative_context(event, investigation.id, result)
    conditions = initiative_conditions(
        cooldown_active=True, can_wait=True, not_before=datetime.now(timezone.utc) + timedelta(seconds=60),
    )
    decision = initiative_decision(event, context, conditions, result=result, response=result.summary)

    assert decision.proposed_action is InitiativeAction.WAIT

    delivered_flags = []

    def execute(action: Action) -> bool:
        delivered_flags.append(action)
        return True

    outcome_row = record_initiative(store, decision=decision, context=context, conditions=conditions, execute=execute)
    assert outcome_row.delivered is False
    assert delivered_flags == []
    assert outcome_row.outcome.status is OutcomeStatus.ALLOWED
    assert "defer" in (outcome_row.outcome.detail or "").lower()


# IN-11 ----------------------------------------------------------------------


def test_in11_silence_decision_produces_no_delivery(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    investigation = stored_completed_investigation(store, question)
    result = completed_result(investigation.id)
    event = initiative_event(investigation.id)
    context = initiative_context(event, investigation.id, result)
    conditions = initiative_conditions(duplicate=True)
    decision = initiative_decision(event, context, conditions, result=result, response=result.summary)

    assert decision.proposed_action is InitiativeAction.SILENCE

    delivered_flags = []

    def execute(action: Action) -> bool:
        delivered_flags.append(action)
        return True

    outcome_row = record_initiative(store, decision=decision, context=context, conditions=conditions, execute=execute)
    assert outcome_row.delivered is False
    assert delivered_flags == []
    assert outcome_row.outcome.status is OutcomeStatus.ALLOWED


# IN-12 ----------------------------------------------------------------------


def test_in12_failed_investigation_produces_no_initiative_delivery(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    investigation = store.create_investigation(question.id, InvestigationBudget(), 3)
    investigation = investigation.transition(InvestigationStatus.RUNNING).transition(
        InvestigationStatus.FAILED, error="search unavailable",
    )
    event = initiative_event(investigation.id)
    context = initiative_context(event, investigation.id, None)
    decision = initiative_decision(event, context, initiative_conditions())

    assert decision.proposed_action is InitiativeAction.SILENCE

    outcome_row = record_initiative(
        store, decision=decision, context=context, conditions=initiative_conditions(),
        execute=lambda action: (_ for _ in ()).throw(AssertionError("must not deliver")),
    )
    assert outcome_row.delivered is False
    assert outcome_row.outcome.status is OutcomeStatus.ALLOWED


# IN-13 ----------------------------------------------------------------------


def test_in13_persistence_failure_is_isolated(tmp_path, monkeypatch):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    investigation = stored_completed_investigation(store, question)
    result = completed_result(investigation.id)
    event = initiative_event(investigation.id)
    context = initiative_context(event, investigation.id, result)
    decision = initiative_decision(event, context, initiative_conditions(), result=result, response=result.summary)

    def boom(*args, **kwargs):
        raise sqlite3.OperationalError("initiative bookkeeping down")

    monkeypatch.setattr(store, "record_companion_outcome", boom)
    with pytest.raises(sqlite3.OperationalError, match="initiative bookkeeping down"):
        record_initiative(
            store, decision=decision, context=context, conditions=initiative_conditions(),
            execute=lambda action: True,
        )


# IN-14 ----------------------------------------------------------------------


def test_in14_runtime_restart_no_duplicate_initiative_delivery(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)

    def make_runtime() -> Runtime:
        runtime = Runtime("fake-model", heartbeat_enabled=False, presence_enabled=True)
        runtime.companion_enabled = True
        runtime.autonomy_enabled = True
        runtime.initiative_enabled = True
        runtime.configure_exploration_worker(
            store, StubExploration(), max_queue_items=10, cooldown_seconds=0.01,
            max_attempts_per_period=3, period_seconds=3600,
        )
        runtime.configure_investigations(store, InvestigationBudget(), 3)
        runtime.configure_investigation_execution(
            store, InvestigationBudget(), 3, FakeSearch(), FakeReader(), FakeProvider(),
        )
        runtime.start()
        return runtime

    runtime_a = make_runtime()
    assert runtime_a._publish_exploration_question(question) is True
    investigation = store.get_investigation_for_question(question.id)
    assert investigation.status is InvestigationStatus.COMPLETED
    runtime_a.shutdown()

    runtime_b = make_runtime()
    delivered_b = runtime_b._maybe_initiate_investigation(investigation.id)
    assert delivered_b is False
    runtime_b.shutdown()


# IN-15 ----------------------------------------------------------------------


def test_in15_initiative_disabled_no_delivery_and_no_execution(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    investigation = stored_completed_investigation(store, question)
    result = completed_result(investigation.id)
    event = initiative_event(investigation.id)
    context = initiative_context(event, investigation.id, result)
    decision = initiative_decision(event, context, initiative_conditions(), result=result, response=result.summary)

    delivered_flags = []

    def execute(action: Action) -> bool:
        delivered_flags.append(action)
        return True

    outcome_row = record_initiative(
        store, decision=decision, context=context, conditions=initiative_conditions(),
        execute=execute, companion_enabled=False,
    )
    assert delivered_flags == []
    assert outcome_row.delivered is False
    assert outcome_row.action is None
    assert outcome_row.persisted is False


# IN-16 ----------------------------------------------------------------------


def test_in16_runtime_integration_autonomous_investigation_triggers_limited_initiative(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime = Runtime("fake-model", heartbeat_enabled=False, presence_enabled=True)
    runtime.companion_enabled = True
    runtime.autonomy_enabled = True
    runtime.initiative_enabled = True
    runtime.configure_exploration_worker(
        store, StubExploration(), max_queue_items=10, cooldown_seconds=0.01,
        max_attempts_per_period=3, period_seconds=3600,
    )
    runtime.configure_investigations(store, InvestigationBudget(), 3)
    runtime.configure_investigation_execution(
        store, InvestigationBudget(), 3, FakeSearch(), FakeReader(), FakeProvider(),
    )
    runtime.start()
    try:
        assert runtime._publish_exploration_question(question) is True
        investigation = store.get_investigation_for_question(question.id)
        assert investigation is not None
        assert investigation.status is InvestigationStatus.COMPLETED
        assert len(store.list_investigations()) == 1
        result = store.get_investigation_result(investigation.id)
        pending = runtime.presence.pending()
        initiative_events = [event for event in pending if event.source_id == "initiative"]
        assert len(initiative_events) == 1

        decision = initiative_decision(
            initiative_event(investigation.id),
            initiative_context(initiative_event(investigation.id), investigation.id, result),
            initiative_conditions(),
            result=result,
            response=result.summary,
        )
        assert decision.proposed_action is InitiativeAction.COMMUNICATE
        outcome_row = store.get_companion_outcome(decision.trigger_event_id)
        assert outcome_row is not None
        assert outcome_row[4] == OutcomeStatus.SUCCEEDED.value
        assert outcome_row[3] is not None
    finally:
        runtime.shutdown()


# IN-17 ----------------------------------------------------------------------


def test_in17_initiative_disabled_at_runtime_creates_no_autonomous_communication(tmp_path):
    """Disabling initiative leaves completed investigations untouched and silent."""
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime = Runtime("fake-model", heartbeat_enabled=False, presence_enabled=True)
    runtime.companion_enabled = True
    runtime.autonomy_enabled = True
    runtime.initiative_enabled = False
    runtime.configure_exploration_worker(
        store, StubExploration(), max_queue_items=10, cooldown_seconds=0.01,
        max_attempts_per_period=3, period_seconds=3600,
    )
    runtime.configure_investigations(store, InvestigationBudget(), 3)
    runtime.configure_investigation_execution(
        store, InvestigationBudget(), 3, FakeSearch(), FakeReader(), FakeProvider(),
    )
    runtime.start()
    try:
        assert runtime._publish_exploration_question(question) is True
        investigation = store.get_investigation_for_question(question.id)
        assert investigation is not None
        assert investigation.status is InvestigationStatus.COMPLETED
        assert runtime.initiative_execution_enabled is False

        pending = runtime.presence.pending()
        assert [event.source_id for event in pending] == ["exploration"]
        trigger = initiative_event(investigation.id)
        assert store.get_companion_outcome(trigger.event_id) is None
        assert runtime._maybe_initiate_investigation(investigation.id) is False
        assert [event.source_id for event in runtime.presence.pending()] == ["exploration"]
    finally:
        runtime.shutdown()
