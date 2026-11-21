from datetime import datetime, timezone

from sylphy.app import _deliver_presence, _presence_command, _prompt_user
from sylphy.presence import PresenceEvent, PresenceEventType
from sylphy.runtime import Runtime
from sylphy.storage import SQLiteStore


def make_event(title="Notice", content="Foreground delivery only"):
    return PresenceEvent(PresenceEventType.NOTICE, title, content)


def test_prompt_delivers_before_blocking_input_and_distinguishes_presence(monkeypatch, capsys):
    runtime = Runtime("model", heartbeat_enabled=False, presence_enabled=True)
    runtime.start()
    item = make_event("A queued notice", "A bounded message.")
    assert runtime.publish_presence(item)
    order = []
    original_print = print

    def record_print(*args, **kwargs):
        order.append(("print", " ".join(str(arg) for arg in args)))
        original_print(*args, **kwargs)

    def record_input(prompt):
        order.append(("input", prompt))
        return "hello"

    monkeypatch.setattr("builtins.print", record_print)
    monkeypatch.setattr("builtins.input", record_input)
    assert _prompt_user(runtime) == "hello"
    assert order[0][0] == "print" and "SYLPHY PRESENCE" in order[0][1]
    assert order[1] == ("input", "You: ")
    assert "A bounded message." in capsys.readouterr().out
    assert runtime.presence.pending() == ()
    runtime.shutdown()


def test_no_background_terminal_output_and_delivery_does_not_change_history(tmp_path, capsys):
    store = SQLiteStore(tmp_path / "cli-presence.sqlite3")
    session = store.create_session()
    now = datetime.now(timezone.utc)
    from sylphy.core import Message
    user = Message("u", session.id, "user", "existing user message", now)
    assistant = Message("a", session.id, "assistant", "existing reply", now)
    store.append_turn(user, assistant)
    runtime = Runtime("model", heartbeat_enabled=False, presence_enabled=True)
    runtime.start()
    runtime.publish_presence(make_event())
    assert capsys.readouterr().out == ""
    before = store.messages(session.id)
    _deliver_presence(runtime)
    assert store.messages(session.id) == before
    assert "[SYLPHY PRESENCE — Notice] Foreground delivery only" in capsys.readouterr().out
    runtime.shutdown()


def test_presence_cli_controls_disable_list_and_dismiss(capsys):
    runtime = Runtime("model", heartbeat_enabled=False, presence_enabled=True)
    runtime.start()
    item = make_event()
    runtime.publish_presence(item)
    _presence_command("/presence disable", runtime)
    assert not runtime.presence.enabled
    assert runtime.presence.pending() == (item,)
    _presence_command("/presence list", runtime)
    assert item.event_id in capsys.readouterr().out
    _presence_command(f"/presence dismiss {item.event_id}", runtime)
    assert runtime.presence.pending() == ()
    _presence_command("/presence enable", runtime)
    assert runtime.presence.enabled
    runtime.shutdown()
