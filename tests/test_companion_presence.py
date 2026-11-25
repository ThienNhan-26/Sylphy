"""Turn 6 Presence and event-driven Companion loop tests (PR-01 to PR-12).

The real event source is an already-saved Exploration question reaching the
existing Runtime presence-notice boundary. Each test uses a temporary SQLite
database and a fake provider; no live model, network, or external service is
required.
"""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from sylphy.companion_contract import (
    CompanionEventSource,
    CompanionEventType,
    CompanionState,
    EvidenceKind,
    OutcomeStatus,
)
from sylphy.companion_slice import (
    _presence_event_id,
    _turn_event_id,
    build_presence_context,
    presence_notice_event,
    propose_presence_decision,
    record_presence_event,
)
from sylphy.config import load_config
from sylphy.context import ContextBuilder
from sylphy.core import ConversationService, Message, Turn
from sylphy.exploration import ExplorationQuestion
from sylphy.interaction_policy import (
    InteractionAction,
    InteractionReason,
    PolicyConditions,
)
from sylphy.runtime import Runtime
from sylphy.storage import SQLiteStore


class FakeProvider:
    def __init__(self, answer="hello"):
        self.answer = answer
        self.messages = None

    def reply(self, messages, cancel=None, on_chunk=None):
        self.messages = messages
        if cancel and cancel.is_set():
            raise InterruptedError("cancelled")
        if on_chunk:
            on_chunk(self.answer)
        return self.answer


class StubExploration:
    """The worker performs no model work and forms no new question."""

    def analyze(self, task, turn, messages, cancel=None):
        return None


def make_store(tmp_path, name="presence.db"):
    store = SQLiteStore(tmp_path / name)
    session = store.create_session("presence-session")
    now = datetime.now(timezone.utc)
    user = Message("presence-user", session.id, "user", "I enjoy learning about gardens.", now)
    assistant = Message(
        "presence-assistant", session.id, "assistant", "Gardens can be fascinating.", now
    )
    store.append_turn(user, assistant)
    return store, session, Turn(session.id, user, assistant)


def make_question(turn, question_id="question-presence-1", text="What makes gardens welcoming?"):
    now = datetime.now(timezone.utc)
    return ExplorationQuestion(
        question_id,
        text,
        turn.session_id,
        (turn.user.id, turn.assistant.id),
        (),
        now,
        "pending",
        0,
        now + timedelta(days=3),
        topic="gardens",
        reason="The conversation raised the topic.",
    )


def configured_runtime(
    store,
    *,
    presence_enabled=True,
    companion_enabled=True,
    capacity=20,
):
    runtime = Runtime(
        "unused-model",
        heartbeat_enabled=False,
        presence_enabled=presence_enabled,
        presence_queue_capacity=capacity,
    )
    runtime.companion_enabled = companion_enabled
    runtime.configure_exploration_worker(
        store,
        StubExploration(),
        max_queue_items=10,
        cooldown_seconds=0.01,
        max_attempts_per_period=3,
        period_seconds=3600,
    )
    runtime.start()
    return runtime


def presence_inputs(question):
    now = datetime.now(timezone.utc)
    event = presence_notice_event(
        notice_id=question.id,
        occurred_at=now,
        source_session_id=question.source_session_id,
        expires_at=question.expires_at,
    )
    context = build_presence_context(event, queue_available=True)
    decision = propose_presence_decision(event, context)
    return event, context, decision


def direct_record(store, question, *, conditions=None, execute=None, companion_enabled=True):
    event, context, decision = presence_inputs(question)
    return record_presence_event(
        store,
        event=event,
        context=context,
        decision=decision,
        conditions=conditions or PolicyConditions(),
        occurred_at=event.occurred_at,
        execute=execute,
        pending_notice_count=lambda: 0,
        evidence=question.id,
        companion_enabled=companion_enabled,
    )


# PR-01 ----------------------------------------------------------------------


def test_pr01_real_event_source_is_the_saved_question_presence_boundary(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime = configured_runtime(store, companion_enabled=True, presence_enabled=True)
    try:
        # The event source already exists durably before the presence decision.
        assert store.get_exploration_question(question.id) is not None
        assert runtime.presence.pending() == ()

        assert runtime._publish_exploration_question(question) is True

        event_id = _presence_event_id(question.id)
        assert store.get_companion_outcome(event_id) is not None
        assert len(runtime.presence.pending()) == 1
    finally:
        runtime.shutdown()


# PR-02 ----------------------------------------------------------------------


def test_pr02_valid_event_becomes_typed_companion_event_and_is_processed(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    event, context, decision = presence_inputs(question)

    assert event.event_type is CompanionEventType.PRESENCE_NOTICE_READY
    assert event.source is CompanionEventSource.PRESENCE
    assert event.payload == {"notice_id": question.id}
    assert event.event_id == _presence_event_id(question.id)
    assert event.occurred_at.tzinfo is not None
    assert context.event is event
    assert decision.event_id == event.event_id

    result = record_presence_event(
        store,
        event=event,
        context=context,
        decision=decision,
        conditions=PolicyConditions(),
        occurred_at=event.occurred_at,
        execute=lambda: True,
        pending_notice_count=lambda: 1,
        evidence=question.id,
    )
    assert result.persisted is True
    assert result.outcome.status is OutcomeStatus.SUCCEEDED
    assert store.get_companion_outcome(event.event_id) is not None


# PR-03 ----------------------------------------------------------------------


def test_pr03_context_items_have_explicit_evidence_classifications(tmp_path):
    _store, _session, turn = make_store(tmp_path)
    question = make_question(turn)
    event, context, _decision = presence_inputs(question)

    kinds = [item.kind for item in context.items]
    assert EvidenceKind.CONFIRMED in kinds
    assert EvidenceKind.CURRENT in kinds
    assert EvidenceKind.MISSING in kinds
    assert EvidenceKind.INFERRED not in kinds

    confirmed = context.confirmed
    assert len(confirmed) == 1
    assert confirmed[0].source_id == question.id
    assert "pending delivery" in confirmed[0].content

    missing = context.missing
    assert len(missing) == 1
    assert missing[0].source_id is None
    assert missing[0].confidence is None
    assert "availability" in missing[0].content

    full_event = presence_notice_event(
        notice_id=question.id,
        occurred_at=event.occurred_at,
        source_session_id=question.source_session_id,
        expires_at=question.expires_at,
    )
    full_context = build_presence_context(full_event, queue_available=False)
    assert any("full" in item.content for item in full_context.current)


# PR-04 ----------------------------------------------------------------------


def test_pr04_decision_is_deterministic(tmp_path):
    _store, _session, turn = make_store(tmp_path)
    question = make_question(turn)
    event, context, first = presence_inputs(question)
    second = propose_presence_decision(event, context)

    assert first.action is InteractionAction.SPEAK
    assert second.action is first.action
    assert second.message == first.message
    assert second.rationale == first.rationale

    store_a = SQLiteStore(tmp_path / "deterministic-a.db")
    store_b = SQLiteStore(tmp_path / "deterministic-b.db")
    result_a = direct_record(store_a, question, execute=lambda: True)
    result_b = direct_record(store_b, question, execute=lambda: True)

    assert result_a.action is result_b.action is InteractionAction.SPEAK
    assert result_a.reason_code is result_b.reason_code
    assert result_a.outcome.status is result_b.outcome.status is OutcomeStatus.SUCCEEDED


# PR-05 ----------------------------------------------------------------------


def test_pr05_existing_interaction_policy_permits_and_rejects(tmp_path):
    _store, _session, turn = make_store(tmp_path)
    question = make_question(turn)
    calls = []

    permitted = direct_record(
        SQLiteStore(tmp_path / "permit.db"),
        question,
        execute=lambda: calls.append("permit") or True,
    )
    assert permitted.action is InteractionAction.SPEAK
    assert permitted.reason_code is InteractionReason.ELIGIBLE_TO_SHARE
    assert permitted.outcome.status is OutcomeStatus.SUCCEEDED
    assert calls == ["permit"]

    denied_calls = []
    denied = direct_record(
        SQLiteStore(tmp_path / "deny.db"),
        question,
        conditions=PolicyConditions(permission_denied=True),
        execute=lambda: denied_calls.append("deny") or True,
    )
    assert denied.action is InteractionAction.SILENCE
    assert denied.reason_code is InteractionReason.PERMISSION_DENIED
    assert denied.outcome.status is OutcomeStatus.REJECTED
    assert denied.outcome.action_id is None
    assert denied_calls == []  # denied decisions never execute


# PR-06 ----------------------------------------------------------------------


def test_pr06_silence_is_a_valid_terminal_decision_without_delivery(tmp_path):
    _store, _session, turn = make_store(tmp_path)
    question = make_question(turn)
    calls = []

    dismissed = direct_record(
        SQLiteStore(tmp_path / "dismissed.db"),
        question,
        conditions=PolicyConditions(dismissed=True),
        execute=lambda: calls.append("dismissed") or True,
    )
    assert dismissed.action is InteractionAction.SILENCE
    assert dismissed.reason_code is InteractionReason.DISMISSED_BY_USER
    assert dismissed.outcome.status is OutcomeStatus.REJECTED
    assert dismissed.published is False
    assert dismissed.outcome.action_id is None
    assert calls == []

    # Companion disabled is a silent, side-effect-free no-op: no outcome, no
    # state, no delivery attempt.
    disabled_store = SQLiteStore(tmp_path / "disabled-pipeline.db")
    disabled = direct_record(
        disabled_store,
        question,
        execute=lambda: calls.append("disabled") or True,
        companion_enabled=False,
    )
    assert disabled.action is InteractionAction.SILENCE
    assert disabled.reason_code is InteractionReason.FEATURE_DISABLED
    assert disabled.persisted is False
    assert disabled.published is False
    assert disabled_store.list_companion_outcomes() == []
    assert disabled_store.get_companion_state() is None
    assert calls == []

    # Insufficient evidence is silenced, not guessed at.
    ungrounded = direct_record(
        SQLiteStore(tmp_path / "ungrounded.db"),
        question,
        conditions=PolicyConditions(grounded=False),
        execute=lambda: calls.append("ungrounded") or True,
    )
    assert ungrounded.action is InteractionAction.SILENCE
    assert ungrounded.reason_code is InteractionReason.INSUFFICIENT_CONFIDENCE
    assert ungrounded.published is False
    assert calls == []


# PR-07 ----------------------------------------------------------------------


def test_pr07_requesting_permission_does_not_grant_or_execute_it(tmp_path):
    from sylphy.companion_contract import Decision

    _store, _session, turn = make_store(tmp_path)
    question = make_question(turn)
    event, context, _speak = presence_inputs(question)
    decision = Decision(
        event_id=event.event_id,
        action=InteractionAction.REQUEST_PERMISSION,
        rationale="Presence delivery needs explicit user consent.",
        permission_scope="presence",
    )
    calls = []
    store = SQLiteStore(tmp_path / "permission.db")

    result = record_presence_event(
        store,
        event=event,
        context=context,
        decision=decision,
        conditions=PolicyConditions(request_permission=True, permission_granted=False),
        occurred_at=event.occurred_at,
        execute=lambda: calls.append("permission") or True,
        pending_notice_count=lambda: 0,
        evidence=question.id,
    )

    assert result.action is InteractionAction.REQUEST_PERMISSION
    assert result.reason_code is InteractionReason.PERMISSION_REQUIRED
    assert result.outcome.status is OutcomeStatus.ALLOWED
    assert result.outcome.action_id is None
    assert result.published is False
    assert calls == []  # asking for permission does not execute the action

    denied_store = SQLiteStore(tmp_path / "permission-denied.db")
    denied_calls = []
    denied = record_presence_event(
        denied_store,
        event=event,
        context=context,
        decision=decision,
        conditions=PolicyConditions(request_permission=True, permission_denied=True),
        occurred_at=event.occurred_at,
        execute=lambda: denied_calls.append("denied") or True,
        pending_notice_count=lambda: 0,
        evidence=question.id,
    )
    assert denied.action is InteractionAction.SILENCE
    assert denied.reason_code is InteractionReason.PERMISSION_DENIED
    assert denied.outcome.status is OutcomeStatus.REJECTED
    assert denied_calls == []


# PR-08 ----------------------------------------------------------------------


def test_pr08_companion_disabled_leaves_companion_presence_inactive(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime = configured_runtime(store, companion_enabled=False, presence_enabled=True)
    try:
        # The pre-existing Exploration -> Presence notice path is independent
        # and unchanged; the new companion presence loop writes nothing.
        assert runtime._publish_exploration_question(question) is True
        assert store.list_companion_outcomes() == []
        assert store.get_companion_state() is None
        assert len(runtime.presence.pending()) == 1
    finally:
        runtime.shutdown()

    config_file = tmp_path / "app.toml"
    config_file.write_text(
        '[sylphy]\ndata_dir="state"\n[ollama]\nmodel="local"\n[companion]\nenabled=false\n',
        encoding="utf-8",
    )
    assert load_config(config_file).companion_enabled is False


# PR-09 ----------------------------------------------------------------------


def test_pr09_duplicate_event_id_does_not_duplicate_notice_or_outcome(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question = store.create_exploration_question(make_question(turn), max_queue_items=10)
    runtime = configured_runtime(store, companion_enabled=True, presence_enabled=True)
    try:
        assert runtime._publish_exploration_question(question) is True
        assert runtime._publish_exploration_question(question) is False

        assert len(runtime.presence.pending()) == 1
        assert len(store.list_companion_outcomes()) == 1
        assert store.get_companion_state()[0] == 1  # one revision, not two

        event, context, decision = presence_inputs(question)
        duplicate = record_presence_event(
            store,
            event=event,
            context=context,
            decision=decision,
            conditions=PolicyConditions(),
            occurred_at=event.occurred_at,
            execute=lambda: True,
            pending_notice_count=lambda: 1,
            evidence=question.id,
        )
        assert duplicate.skipped_duplicate is True
        assert duplicate.persisted is False
        assert duplicate.published is False
        assert len(store.list_companion_outcomes()) == 1
    finally:
        runtime.shutdown()


# PR-10 ----------------------------------------------------------------------


def test_pr10_presence_failure_does_not_corrupt_persisted_conversation(tmp_path, monkeypatch):
    store, session, _turn = make_store(tmp_path)
    service = ConversationService(
        FakeProvider("assistant reply"), store, ContextBuilder("system", "identity", 10000)
    )
    runtime = configured_runtime(store, companion_enabled=True, presence_enabled=True)
    try:
        first = runtime.run_conversation(service, session.id, "hello", persistence=store)
        assert len(store.list_companion_outcomes()) == 1
        state_before = store.get_companion_state()
        assert state_before[0] == 1

        question = store.create_exploration_question(make_question(_turn), max_queue_items=10)

        def boom(*_args, **_kwargs):
            raise sqlite3.OperationalError("presence bookkeeping down")

        monkeypatch.setattr("sylphy.runtime.record_presence_event", boom)
        assert runtime._publish_exploration_question(question) is False

        # The already-persisted conversation and its companion outcome remain
        # intact; no new outcome or revision is fabricated by the failure.
        messages = store.messages(session.id)
        assert len(messages) == 4  # pre-existing pair + one completed conversation turn
        assert messages[3].id == first.assistant.id
        assert messages[3].content == "assistant reply"
        assert len(store.list_companion_outcomes()) == 1
        assert store.get_companion_state()[0] == 1
    finally:
        runtime.shutdown()


# PR-11 ----------------------------------------------------------------------


def test_pr11_presence_makes_no_unsupported_claims_about_the_user(tmp_path):
    store, _session, turn = make_store(tmp_path)
    question_text = "What makes gardens welcoming?"
    question = store.create_exploration_question(
        make_question(turn, text=question_text), max_queue_items=10
    )
    runtime = configured_runtime(store, companion_enabled=True, presence_enabled=True)
    try:
        assert runtime._publish_exploration_question(question) is True

        notice = runtime.presence.pending()[0]
        assert notice.content == question_text  # exactly the saved question

        outcome_row = store.list_companion_outcomes()[0]
        detail = outcome_row[5] or ""
        for forbidden in (
            "noticed you were away",
            "was waiting for you",
            "saw you come back",
            "have been watching",
            "missed you",
            "continuous awareness",
            "offline activity",
        ):
            assert forbidden not in detail.casefold()
            assert forbidden not in notice.content.casefold()
        assert question_text not in detail  # no user text in durable detail

        event, context, decision = presence_inputs(question)
        assert event.to_dict()["payload"] == {"notice_id": question.id}
        assert context.confirmed[0].source_id == question.id
        assert context.inferred == ()
        assert "waited" not in decision.message.casefold()

        state = CompanionState()
        for forbidden_attr in (
            "awareness",
            "consciousness",
            "emotion",
            "waiting",
            "offline_activity",
            "continuous_presence",
            "continuous_awareness",
        ):
            assert not hasattr(state, forbidden_attr)
    finally:
        runtime.shutdown()


# PR-12 ----------------------------------------------------------------------


def test_pr12_runtime_integration_end_to_end_with_fake_provider(tmp_path):
    store, session, turn = make_store(tmp_path)
    service = ConversationService(
        FakeProvider("assistant reply"), store, ContextBuilder("system", "identity", 10000)
    )
    runtime = configured_runtime(store, companion_enabled=True, presence_enabled=True)
    try:
        result = runtime.run_conversation(service, session.id, "hello", persistence=store)
        question = store.create_exploration_question(make_question(turn), max_queue_items=10)

        assert runtime._publish_exploration_question(question) is True

        messages = store.messages(session.id)
        assert len(messages) == 4  # pre-existing pair + one completed conversation turn
        assert messages[3].id == result.assistant.id

        rows = store.list_companion_outcomes()
        assert len(rows) == 2  # completed turn + presence notice
        presence_event_id = _presence_event_id(question.id)
        assert {row[1] for row in rows} == {
            _turn_event_id(session.id, result.assistant.id),
            presence_event_id,
        }
        assert all(row[4] == OutcomeStatus.SUCCEEDED.value for row in rows)

        state = store.get_companion_state()
        assert state[0] == 2  # one revision per recorded event
        assert state[1] == presence_event_id
        assert state[4] == 0  # conversation_active is a historical False
        assert state[5] == 1  # pending presence notices snapshot
        assert len(runtime.presence.pending()) == 1
    finally:
        runtime.shutdown()
