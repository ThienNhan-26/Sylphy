"""Small client CLI; its process lifetime is independent of the Entity Daemon."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from sylphy_entity.runtime.paths import default_runtime_dir


def _read_token() -> str:
    token_path = default_runtime_dir() / "control.token"
    try:
        return token_path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise RuntimeError(
            f"Cannot read Entity control token at {token_path}; is the Daemon running?"
        ) from exc


def _request(url: str, *, method: str = "GET", payload: dict | None = None, authorized: bool = False):
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if authorized:
        headers["X-Sylphy-Control-Token"] = _read_token()
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=5.0) as response:
        body = response.read()
        return json.loads(body.decode("utf-8")) if body else {}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Control the local SYLPHY Entity Daemon")
    parser.add_argument("--url", default="http://127.0.0.1:8765", help="Entity API base URL")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status", help="Show daemon state")
    subparsers.add_parser("world", help="Show the current World Model snapshot")
    subparsers.add_parser("cognition", help="Show local cognition provider and request status")
    memory = subparsers.add_parser("memory", help="Inspect confirmed and pending memory metadata")
    memory.add_argument("--limit", type=int, default=50)
    subparsers.add_parser("voice", help="Show Voice Output playback status")
    subparsers.add_parser("diagnostics", help="Show the complete Entity diagnostic snapshot")
    subparsers.add_parser("pause", help="Pause autonomous work")
    subparsers.add_parser("resume", help="Resume autonomous work (only if autonomy is opted in)")
    permission = subparsers.add_parser("permission", help="Revoke or restore an explicit permission scope")
    permission.add_argument("action", choices=("revoke", "restore"))
    permission.add_argument("--scope", default=None, help="Exact sandbox scope; omit only for the default scope")
    subparsers.add_parser("stop", help="Request a graceful daemon shutdown")
    chat = subparsers.add_parser("chat", help="Send one text message to Conversation Runtime")
    chat.add_argument("text")
    chat.add_argument("--session", default="default")
    history = subparsers.add_parser("history", help="Read persisted conversation history")
    history.add_argument("--session", default="default")
    history.add_argument("--limit", type=int, default=40)
    speak = subparsers.add_parser("speak", help="Request local Voice Output")
    speak.add_argument("text")
    speak.add_argument("--voice", default="af_heart")
    speak.add_argument("--language", default="en-us")
    speak.add_argument("--speed", type=float, default=1.0)
    subparsers.add_parser("voice-stop", help="Interrupt active Voice Output")
    avatar = subparsers.add_parser("avatar", help="Inspect presentation or manage the separate Avatar process")
    avatar.add_argument("action", nargs="?", choices=("presentation", "status", "start", "stop", "restart"),
                        default="presentation")
    publish = subparsers.add_parser("publish", help="Publish a non-executable runtime event")
    publish.add_argument("--type", required=True, dest="event_type")
    publish.add_argument("--source", required=True)
    publish.add_argument("--payload-json", default=None, help="JSON object payload")
    publish.add_argument("--payload-base64", default=None, help="UTF-8 JSON object payload encoded as Base64")
    return parser


def main() -> None:
    args = _build_parser().parse_args()
    base_url = args.url.rstrip("/")
    try:
        if args.command == "status":
            result = _request(f"{base_url}/status")
        elif args.command == "world":
            result = _request(f"{base_url}/world")
        elif args.command == "cognition":
            result = _request(f"{base_url}/status").get("cognition", {})
        elif args.command == "memory":
            if not 1 <= args.limit <= 100:
                raise ValueError("--limit must be between 1 and 100")
            result = _request(f"{base_url}/memory/status?limit={args.limit}")
        elif args.command == "voice":
            result = _request(f"{base_url}/voice/status")
        elif args.command == "diagnostics":
            result = _request(f"{base_url}/status")
        elif args.command == "pause":
            result = _request(f"{base_url}/autonomy/pause", method="POST", authorized=True)
        elif args.command == "resume":
            result = _request(f"{base_url}/autonomy/resume", method="POST", authorized=True)
        elif args.command == "permission":
            endpoint = "revoke" if args.action == "revoke" else "restore"
            result = _request(
                f"{base_url}/permissions/{endpoint}", method="POST",
                payload={"scope": args.scope}, authorized=True,
            )
        elif args.command == "chat":
            result = _request(
                f"{base_url}/chat", method="POST",
                payload={"text": args.text, "session_id": args.session}, authorized=True,
            )
        elif args.command == "history":
            query = urlencode({"session_id": args.session, "limit": args.limit})
            result = _request(f"{base_url}/conversation/history?{query}")
        elif args.command == "speak":
            result = _request(
                f"{base_url}/voice/speak", method="POST",
                payload={"text": args.text, "voice": args.voice,
                         "language": args.language, "speed": args.speed},
                authorized=True,
            )
        elif args.command == "voice-stop":
            result = _request(f"{base_url}/voice/stop", method="POST", authorized=True)
        elif args.command == "avatar":
            if args.action == "presentation":
                result = _request(f"{base_url}/avatar/presentation")
            else:
                from sylphy_entity.embodiment import avatar_service
                action = "status" if args.action == "status" else args.action
                code = getattr(avatar_service, action)()
                if code != 0:
                    raise RuntimeError(f"Avatar {action} failed with exit code {code}")
                return
        elif args.command == "stop":
            result = _request(f"{base_url}/shutdown", method="POST", authorized=True)
            print(json.dumps(result, indent=2))
            # Allow the detached daemon a short window to finish its graceful shutdown.
            for _ in range(100):
                try:
                    _request(f"{base_url}/health")
                except (HTTPError, URLError, OSError):
                    break
                time.sleep(0.05)
            return
        else:
            raw_payload = args.payload_json
            if args.payload_base64 is not None:
                try:
                    raw_payload = base64.b64decode(args.payload_base64, validate=True).decode("utf-8")
                except (ValueError, UnicodeDecodeError) as exc:
                    raise ValueError(f"--payload-base64 is invalid: {exc}") from exc
            if raw_payload is None:
                raw_payload = "{}"
            try:
                payload = json.loads(raw_payload)
            except json.JSONDecodeError as exc:
                raise ValueError(f"payload is invalid JSON: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError("--payload-json must decode to a JSON object")
            result = _request(
                f"{base_url}/events",
                method="POST",
                payload={
                    "event_type": args.event_type,
                    "source": args.source,
                    "payload": payload,
                },
                authorized=True,
            )
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (HTTPError, URLError, OSError, RuntimeError, ValueError) as exc:
        print(f"SYLPHY Entity CLI: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
