from __future__ import annotations

import json

import pytest

from sylphy_entity.interface import cli


@pytest.mark.parametrize(
    ("argv", "expected_url", "expected_method", "expected_authorized", "expected_payload"),
    [
        (["chat", "hello", "--session", "demo"], "/chat", "POST", True,
         {"text": "hello", "session_id": "demo"}),
        (["speak", "hello", "--voice", "af_heart"], "/voice/speak", "POST", True,
         {"text": "hello", "voice": "af_heart", "language": "en-us", "speed": 1.0}),
        (["voice-stop"], "/voice/stop", "POST", True, None),
        (["avatar"], "/avatar/presentation", "GET", False, None),
    ],
)
def test_entityctl_conversation_voice_and_avatar_commands(
    monkeypatch, capsys, argv, expected_url, expected_method, expected_authorized, expected_payload
):
    captured = {}

    def fake_request(url, *, method="GET", payload=None, authorized=False):
        captured.update(url=url, method=method, payload=payload, authorized=authorized)
        return {"ok": True}

    monkeypatch.setattr(cli, "_request", fake_request)
    monkeypatch.setattr("sys.argv", ["sylphy-entityctl", *argv])
    cli.main()

    assert captured["url"].endswith(expected_url)
    assert captured["method"] == expected_method
    assert captured["authorized"] is expected_authorized
    if expected_payload is not None:
        assert captured["payload"] == expected_payload
    assert json.loads(capsys.readouterr().out) == {"ok": True}


@pytest.mark.parametrize(
    ("argv", "expected_url", "expected_method", "expected_authorized", "expected_payload"),
    [
        (["cognition"], "/status", "GET", False, None),
        (["memory", "--limit", "12"], "/memory/status?limit=12", "GET", False, None),
        (["voice"], "/voice/status", "GET", False, None),
        (["diagnostics"], "/status", "GET", False, None),
        (["pause"], "/autonomy/pause", "POST", True, None),
        (["resume"], "/autonomy/resume", "POST", True, None),
        (["permission", "revoke", "--scope", "sandbox:C:/safe"], "/permissions/revoke", "POST", True,
         {"scope": "sandbox:C:/safe"}),
    ],
)
def test_entityctl_inspection_and_control_commands(
    monkeypatch, capsys, argv, expected_url, expected_method, expected_authorized, expected_payload
):
    captured = {}

    def fake_request(url, *, method="GET", payload=None, authorized=False):
        captured.update(url=url, method=method, payload=payload, authorized=authorized)
        return {"ok": True, "cognition": {"enabled": True}}

    monkeypatch.setattr(cli, "_request", fake_request)
    monkeypatch.setattr("sys.argv", ["sylphy-entityctl", *argv])
    cli.main()

    assert captured["url"].endswith(expected_url)
    assert captured["method"] == expected_method
    assert captured["authorized"] is expected_authorized
    assert captured["payload"] == expected_payload
    output = json.loads(capsys.readouterr().out)
    assert output == ({"enabled": True} if argv[0] == "cognition" else {"ok": True, "cognition": {"enabled": True}})
