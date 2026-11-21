from datetime import datetime, timedelta, timezone
import json
from threading import Event, Thread
import time

import pytest

from sylphy.config import load_config
from sylphy.context import ContextBuilder
from sylphy.core import ConversationService, Message, Turn
from sylphy.exploration import ExplorationQuestion, ExplorationResponseError, ExplorationService
from sylphy.memory import MemoryService
from sylphy.runtime import EventType, Runtime, RuntimePhase
from sylphy.storage import SQLiteStore


def make_turn(store, text="I am building a local-first companion project.", suffix="1"):
    session = store.create_session()
    now = datetime.now(timezone.utc)
    turn = Turn(session.id, Message("user-" + suffix, session.id, "user", text, now),
                Message("assistant-" + suffix, session.id, "assistant", "That sounds interesting.", now))
    store.append_turn(turn.user, turn.assistant)
    return turn


def enqueue(store, turn, *, capacity=20, cooldown=0.01, attempts=3, period=3600):
    return store.enqueue_exploration_task(turn.session_id, (turn.user.id, turn.assistant.id),
                                          capacity, cooldown, attempts, period)


class ReplyProvider:
    def __init__(self, response):
        self.response = response
        self.requests = []

    def reply(self, messages, cancel=None, on_chunk=None):
        self.requests.append(messages)
        if cancel and cancel.is_set():
            raise InterruptedError("cancelled")
        if isinstance(self.response, BaseException):
            raise self.response
        return self.response


def response(question="What makes this project important to the user?", evidence="local-first companion project",
             reason="The project is mentioned."):
    return json.dumps({"question": question, "topic": "local companion", "reason": reason,
                       "evidence": evidence})


def test_grounded_question_persists_provenance_without_changing_memory(tmp_path):
    store = SQLiteStore(tmp_path / "exploration.db")
    turn = make_turn(store)
    memories = MemoryService(store)
    memory = memories.create("The local-first companion project is SYLPHY", "project")
    task = enqueue(store, turn)
    provider = ReplyProvider(response(evidence="The local-first companion project is SYLPHY"))
    service = ExplorationService(provider, store, memories, context_max_chars=1000)

    saved = service.analyze(task, turn, store.messages(turn.session_id))

    assert saved is not None
    assert saved.question == "What makes this project important to the user?"
    assert saved.status == "pending" and saved.attempt_count == 0
    assert saved.source_session_id == turn.session_id
    assert saved.source_message_ids == (turn.user.id, turn.assistant.id)
    assert saved.source_memory_ids == (memory.id,)
    assert memories.list() == [memory]
    prompt = provider.requests[0][1]["content"]
    instructions = provider.requests[0][0]["content"]
    assert "untrusted" in instructions
    assert "at most 400 characters" in instructions
    assert "at most 80 characters" in instructions
    assert "at most 240 characters" in instructions
    assert "at most 300 characters" in instructions
    assert '{"question":null,"topic":null,"reason":null,"evidence":null}' in instructions
    assert memory.content in prompt


def test_empty_result_is_successful_without_question(tmp_path):
    store = SQLiteStore(tmp_path / "empty.db")
    turn = make_turn(store, "Hello, how are you?", "empty")
    provider = ReplyProvider('{"question":null,"topic":null,"reason":null,"evidence":null}')
    runtime = setup_runtime(store, provider)
    task = runtime.exploration_worker.enqueue(turn)
    assert task is not None
    assert wait_for(lambda: store.get_exploration_task(task.id).status == "completed")
    runtime.shutdown()
    assert store.list_exploration_questions() == []
    assert "Do not put explanations" in provider.requests[0][0]["content"]


def test_reason_length_240_accepted_and_241_rejected(tmp_path):
    store = SQLiteStore(tmp_path / "reason-length.db")
    turn = make_turn(store)
    valid_service = ExplorationService(ReplyProvider(response(reason="r" * 240)), store)
    assert valid_service.analyze(enqueue(store, turn), turn, store.messages(turn.session_id)) is not None

    other_turn = make_turn(store, suffix="reason241")
    invalid_service = ExplorationService(ReplyProvider(response(reason="r" * 241)), store)
    with pytest.raises(ExplorationResponseError, match="reason must be null or at most 240"):
        invalid_service.analyze(enqueue(store, other_turn), other_turn, store.messages(other_turn.session_id))


def test_streamed_output_limit_cancels_provider(tmp_path):
    store = SQLiteStore(tmp_path / "output-limit.db")
    turn = make_turn(store)
    task = enqueue(store, turn)
    cancelled = Event()

    class StreamingProvider:
        def reply(self, _messages, cancel=None, on_chunk=None):
            on_chunk("x" * 1011)
            if cancel.is_set():
                cancelled.set()
                raise InterruptedError("stream stopped at output bound")
            return "{}"

    with pytest.raises(ExplorationResponseError, match="bounded output"):
        ExplorationService(StreamingProvider(), store, question_max_length=10).analyze(
            task, turn, store.messages(turn.session_id))
    assert cancelled.is_set()


def test_context_is_bounded_and_full_current_user_message_is_required(tmp_path):
    store = SQLiteStore(tmp_path / "bounded.db")
    turn = make_turn(store, "The current project is a local companion.", "bounded")
    provider = ReplyProvider(response(evidence="The current project is a local companion."))
    service = ExplorationService(provider, store, context_max_chars=130)
    service.analyze(enqueue(store, turn), turn, store.messages(turn.session_id))
    payload = json.loads(provider.requests[0][1]["content"].split("\n", 1)[1])
    conversation = payload["conversation"]
    assert len(json.dumps(payload, ensure_ascii=False)) <= 130
    assert any(item["role"] == "user" and item["content"] == turn.user.content for item in conversation)

    oversized = make_turn(store, "x" * 131, "oversized")
    never_called = ReplyProvider(response())
    with pytest.raises(ValueError, match="Current user message exceeds"):
        ExplorationService(never_called, store, context_max_chars=130).analyze(
            enqueue(store, oversized), oversized, store.messages(oversized.session_id))
    assert never_called.requests == []


@pytest.mark.parametrize("output,match", [
    ("not json", "valid JSON"),
    ('{"question":"A question?"}', "exactly"),
    (response("x" * 80, "local-first companion project"), "configured length"),
    (json.dumps({"question": "Grounded?", "topic": None, "reason": None,
                 "evidence": "This does not occur in the conversation"}), "not present"),
    ('{"question":null,"topic":"invented","reason":null,"evidence":null}', "null optional"),
    ("x" * 1041, "bounded output"),
])
def test_invalid_or_unsupported_outputs_are_rejected(tmp_path, output, match):
    store = SQLiteStore(tmp_path / (str(abs(hash(output))) + ".db"))
    turn = make_turn(store)
    task = enqueue(store, turn)
    service = ExplorationService(ReplyProvider(output), store, question_max_length=40)
    with pytest.raises(ExplorationResponseError, match=match):
        service.analyze(task, turn, store.messages(turn.session_id))
    assert store.list_exploration_questions() == []


def make_question(text, *, expires=None, created=None, status="pending"):
    now = created or datetime.now(timezone.utc)
    return ExplorationQuestion("q-" + str(abs(hash(text))), text, "session", ("user", "assistant"), (), now,
                               status, 0, expires or now + timedelta(days=2), "topic", "reason")


def test_question_record_rejects_invalid_status_and_expiry():
    with pytest.raises(ValueError, match="status"):
        make_question("What is worth understanding?", status="running")
    now = datetime.now(timezone.utc)
    with pytest.raises(ValueError, match="expiry"):
        ExplorationQuestion("bad", "Question?", "session", (), (), now, "pending", 0,
                            now - timedelta(seconds=1))


def test_question_dedup_capacity_expiry_dismissal_and_restart(tmp_path):
    path = tmp_path / "queue.db"
    store = SQLiteStore(path)
    first = store.create_exploration_question(make_question("What matters here?"), 1)
    duplicate = store.create_exploration_question(make_question("  WHAT   matters here? "), 1)
    assert duplicate.id == first.id
    assert store.create_exploration_question(make_question("A different question?"), 1) is None

    expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    expired = make_question("An expired question?", created=expired_at - timedelta(days=1), expires=expired_at)
    other_store = SQLiteStore(tmp_path / "expiry.db")
    other_store.create_exploration_question(expired, 1)
    replacement = other_store.create_exploration_question(make_question("A replacement question?"), 1)
    assert replacement is not None
    assert other_store.list_exploration_questions(None)[0].status == "expired"

    restarted = SQLiteStore(path)
    assert restarted.get_exploration_question(first.id) == first
    assert restarted.dismiss_exploration_question(first.id).status == "dismissed"
    assert restarted.list_exploration_questions() == []
    with pytest.raises(ValueError, match="dismissed"):
        restarted.dismiss_exploration_question(first.id)


def test_task_dedup_cooldown_attempt_budget_and_recovery(tmp_path):
    store = SQLiteStore(tmp_path / "limits.db")
    turn = make_turn(store)
    task = enqueue(store, turn, cooldown=60, attempts=1)
    assert enqueue(store, turn, cooldown=60, attempts=1).id == task.id
    assert store.enqueue_exploration_task("another", ("u", "a"), 20, 60, 1, 3600) is None
    claimed, delay = store.claim_exploration_task(60, 1, 3600)
    assert claimed.status == "running" and claimed.attempt_count == 1 and delay == 0
    restarted = SQLiteStore(tmp_path / "limits.db")
    assert restarted.recover_exploration_tasks() == 1
    recovered, delay = restarted.claim_exploration_task(60, 1, 3600)
    assert recovered is None and delay > 0
    assert store.get_exploration_question("missing") is None


def test_exploration_config_defaults_disabled_and_validates_limits(tmp_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    path = tmp_path / "config.toml"
    path.write_text("[ollama]\nmodel='local'\n", encoding="utf-8")
    config = load_config(path)
    assert config.exploration_enabled is False
    assert config.exploration_max_queue_items == 20
    path.write_text("[exploration]\nenabled=true\ncooldown_seconds=0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="cooldown_seconds"):
        load_config(path)
    path.write_text("[exploration]\nenabled='yes'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="exploration.enabled"):
        load_config(path)
    path.write_text("[exploration]\nmax_queue_items=true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="max_queue_items must be an integer"):
        load_config(path)
    path.write_text("[exploration]\nenabled=true\n[ollama]\nhost='http://example.test:11434'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="loopback Ollama host"):
        load_config(path)


def test_local_ollama_shorthand_is_normalized_when_exploration_enabled(tmp_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    path = tmp_path / "local-host.toml"
    path.write_text('[ollama]\nhost="127.0.0.1:11434"\nmodel="local"\n'
                    '[exploration]\nenabled=true\n', encoding="utf-8")
    config = load_config(path)
    assert config.ollama_host == "http://127.0.0.1:11434"
    path.write_text('[ollama]\nhost="[::1]:11434"\nmodel="local"\n'
                    '[exploration]\nenabled=true\n', encoding="utf-8")
    assert load_config(path).ollama_host == "http://[::1]:11434"


@pytest.mark.parametrize("host", [
    "http://example.test:11434",
    "http://user:pass@127.0.0.1:11434",
    "ftp://127.0.0.1:11434",
    "http://127.0.0.1:0",
    "http://127.0.0.1:65536",
    "http://127.0.0.1:notaport",
    "http:///127.0.0.1:11434",
    "http://[::1",
    "http://127.0.0.1:11434/api",
    "http://127.0.0.1:11434?query=1",
    "http://127.0.0.1:11434#fragment",
    "http://127.0.0.1:",
])
def test_exploration_rejects_remote_credentials_bad_scheme_and_malformed_host(tmp_path, monkeypatch, host):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    path = tmp_path / "invalid-host.toml"
    path.write_text(f'[ollama]\nhost={json.dumps(host)}\nmodel="local"\n'
                    '[exploration]\nenabled=true\n', encoding="utf-8")
    with pytest.raises(ValueError, match="loopback Ollama host"):
        load_config(path)


def test_remote_ollama_host_remains_accepted_while_exploration_is_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    path = tmp_path / "disabled-remote.toml"
    path.write_text('[ollama]\nhost="example.test:11434"\nmodel="local"\n'
                    '[exploration]\nenabled=false\n', encoding="utf-8")
    config = load_config(path)
    assert config.exploration_enabled is False
    assert config.ollama_host == "http://example.test:11434"


def wait_for(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        Event().wait(0.005)
    return predicate()


def setup_runtime(store, provider, *, cooldown=0.05, attempts=3, shutdown_grace=1):
    runtime = Runtime("fake-model", heartbeat_enabled=False)
    service = ExplorationService(provider, store, context_max_chars=1000)
    runtime.configure_exploration_worker(store, service, 10, cooldown, attempts, 3600,
                                         expiry_days=30, shutdown_grace_seconds=shutdown_grace)
    runtime.start()
    return runtime


def test_runtime_enqueues_after_turn_persistence_and_returns_while_exploration_runs(tmp_path):
    store = SQLiteStore(tmp_path / "runtime.db")
    session = store.create_session()
    entered, release = Event(), Event()

    class Provider:
        def reply(self, messages, cancel=None, on_chunk=None):
            if "Configured identity:" in messages[0]["content"]:
                return "A completed answer"
            entered.set()
            release.wait(2)
            return response()

    provider = Provider()
    runtime = setup_runtime(store, provider)
    conversation = ConversationService(provider, store, ContextBuilder("system", "identity", 1000))
    result = []
    finished = Event()
    thread = Thread(target=lambda: (result.append(runtime.run_conversation(conversation, session.id,
                                                                           "I am building a local-first companion project.")),
                                    finished.set()))
    thread.start()
    try:
        assert entered.wait(1)
        assert finished.wait(1)
        assert result and len(store.messages(session.id)) == 2
        queued = store.list_exploration_tasks()
        assert len(queued) == 1
        assert queued[0].status in {"pending", "running"}
        release.set()
        assert wait_for(lambda: store.get_exploration_task(queued[0].id).status == "completed")
    finally:
        release.set()
        thread.join(1)
        runtime.shutdown()
    assert len(store.list_exploration_questions()) == 1
    assert store.exploration_counts().get("task_completed") == 1
    assert store.list_memories() == []


def test_conversation_preempts_exploration_and_gets_shared_slot(tmp_path):
    store = SQLiteStore(tmp_path / "priority.db")
    turn = make_turn(store)
    task = enqueue(store, turn)
    explore_entered, release_explore, resource_wait = Event(), Event(), Event()

    class Provider:
        def reply(self, messages, cancel=None, on_chunk=None):
            if "Configured identity:" in messages[0]["content"]:
                return "foreground answer"
            explore_entered.set()
            release_explore.wait(2)
            if cancel and cancel.is_set():
                raise InterruptedError("preempted")
            return response()

    provider = Provider()
    runtime = setup_runtime(store, provider, attempts=1)
    runtime.events.subscribe(EventType.RESOURCE_LIMIT_REACHED, lambda _event: resource_wait.set())
    assert explore_entered.wait(1)
    session = store.create_session()
    conversation = ConversationService(provider, store, ContextBuilder("system", "identity", 1000))
    result = []
    thread = Thread(target=lambda: result.append(runtime.run_conversation(conversation, session.id, "foreground")))
    thread.start()
    try:
        assert resource_wait.wait(1)
        release_explore.set()
        thread.join(2)
        assert not thread.is_alive()
        assert result[0].assistant.content == "foreground answer"
    finally:
        release_explore.set()
        runtime.shutdown()
    assert store.get_exploration_task(task.id).status == "pending"
    assert store.exploration_counts().get("task_pending") == 1


def test_shutdown_cancels_exploration_and_leaves_recoverable_task(tmp_path):
    store = SQLiteStore(tmp_path / "shutdown.db")
    turn = make_turn(store)
    task = enqueue(store, turn)
    entered, release = Event(), Event()

    class Provider:
        def reply(self, _messages, cancel=None, on_chunk=None):
            entered.set()
            release.wait(2)
            if cancel and cancel.is_set():
                raise InterruptedError("shutdown")
            return response()

    runtime = setup_runtime(store, Provider(), shutdown_grace=1)
    assert entered.wait(1)
    shutting_down = Event()
    runtime.events.subscribe(EventType.RUNTIME_SHUTTING_DOWN, lambda _event: (shutting_down.set(), release.set()))
    shutdown_thread = Thread(target=runtime.shutdown)
    shutdown_thread.start()
    assert shutting_down.wait(1)
    assert runtime.exploration_worker._stop.wait(1)
    release.set()
    shutdown_thread.join(2)
    assert not shutdown_thread.is_alive()
    assert runtime.state.snapshot().phase == RuntimePhase.STOPPED
    assert store.get_exploration_task(task.id).status == "pending"
    assert store.list_exploration_questions() == []


def test_exploration_failure_does_not_invalidate_completed_conversation(tmp_path):
    store = SQLiteStore(tmp_path / "failure.db")
    session = store.create_session()
    failed = Event()

    class Provider:
        def reply(self, messages, cancel=None, on_chunk=None):
            if "Configured identity:" in messages[0]["content"]:
                return "saved response"
            failed.set()
            raise RuntimeError("local model unavailable")

    provider = Provider()
    runtime = setup_runtime(store, provider)
    conversation = ConversationService(provider, store, ContextBuilder("system", "identity", 1000))
    result = runtime.run_conversation(conversation, session.id, "A local-first companion project.")
    assert result.assistant.content == "saved response"
    assert failed.wait(1)
    assert wait_for(lambda: store.exploration_counts().get("task_failed") == 1)
    runtime.shutdown()
    assert [message.content for message in store.messages(session.id)] == ["A local-first companion project.", "saved response"]
    assert store.exploration_counts().get("task_failed") == 1


def test_cli_exploration_commands_inspect_and_dismiss(tmp_path, capsys):
    from sylphy.app import _exploration_command

    store = SQLiteStore(tmp_path / "cli.db")
    question = store.create_exploration_question(make_question("What is worth understanding?"), 5)
    runtime = Runtime("model", heartbeat_enabled=False)
    _exploration_command("/exploration status", runtime, store)
    assert "Exploration: disabled" in capsys.readouterr().out
    _exploration_command("/exploration list", runtime, store)
    assert question.question in capsys.readouterr().out
    _exploration_command(f"/exploration dismiss {question.id}", runtime, store)
    assert "dismissed" in capsys.readouterr().out
