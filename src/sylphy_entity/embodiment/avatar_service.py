"""Independent Windows Avatar desktop process supervisor.

The Entity daemon remains authoritative and is never spawned, stopped or owned here.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover - environment diagnostic
    psutil = None

ROOT = Path(__file__).resolve().parents[3]
AVATAR_DIR = ROOT / "avatar"
STATE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "SYLPHY" / "Avatar"
STATE_FILE = STATE_DIR / "service.json"
WINDOWS = os.name == "nt"


def _load() -> dict[str, Any]:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save(data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temporary = STATE_FILE.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(STATE_FILE)


def _process_matches(pid: int, created: float) -> bool:
    if psutil is None:
        return False
    try:
        proc = psutil.Process(pid)
        return abs(proc.create_time() - created) < 1.0 and proc.is_running()
    except psutil.Error:
        return False


def status() -> int:
    state = _load()
    pid, created = state.get("pid"), state.get("create_time")
    running = isinstance(pid, int) and isinstance(created, (int, float)) and _process_matches(pid, float(created))
    print(json.dumps({"running": running, "pid": pid if running else None, "state_file": str(STATE_FILE)}, indent=2))
    return 0


def start() -> int:
    if not WINDOWS:
        print("Desktop shell acceptance requires Windows; this command is Windows-only.", file=sys.stderr)
        return 2
    state = _load()
    pid, created = state.get("pid"), state.get("create_time")
    if isinstance(pid, int) and isinstance(created, (int, float)) and _process_matches(pid, float(created)):
        print(f"Avatar already running (PID {pid}).")
        return 0
    if not (AVATAR_DIR / "node_modules" / "electron" / "dist" / "electron.exe").exists():
        print("Electron is not installed. Run: cd avatar; npm install", file=sys.stderr)
        return 2
    if not (AVATAR_DIR / "dist" / "index.html").exists():
        print("Production renderer missing. Run: cd avatar; npm run desktop:build", file=sys.stderr)
        return 2
    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    log_dir = STATE_DIR / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = open(log_dir / "avatar.log", "ab", buffering=0)
    proc = subprocess.Popen(
        [str(AVATAR_DIR / "node_modules" / "electron" / "dist" / "electron.exe"), str(AVATAR_DIR)],
        cwd=AVATAR_DIR, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
        creationflags=flags, close_fds=True,
    )
    time.sleep(1.2)
    if proc.poll() is not None:
        print(f"Avatar exited during startup ({proc.returncode}); inspect {log_dir / 'avatar.log'}", file=sys.stderr)
        return 1
    created_time = psutil.Process(proc.pid).create_time() if psutil else time.time()
    _save({"pid": proc.pid, "create_time": created_time, "started_at": time.time(), "log": str(log_dir / "avatar.log")})
    print(f"Avatar started (PID {proc.pid}). Entity daemon was not touched.")
    return 0


def stop() -> int:
    state = _load()
    pid, created = state.get("pid"), state.get("create_time")
    if not isinstance(pid, int) or not isinstance(created, (int, float)) or not _process_matches(pid, float(created)):
        print("Avatar is not running.")
        return 0
    try:
        proc = psutil.Process(pid)
        children = proc.children(recursive=True)
        for child in reversed(children):
            child.terminate()
        proc.terminate()
        gone, alive = psutil.wait_procs([proc, *children], timeout=5)
        for item in alive:
            item.kill()
    except psutil.Error as exc:
        print(f"Could not stop verified Avatar process: {exc}", file=sys.stderr)
        return 1
    _save({})
    print("Avatar stopped. Entity daemon remains running.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage only the SYLPHY Avatar desktop process.")
    parser.add_argument("action", choices=("start", "stop", "restart", "status"))
    args = parser.parse_args()
    if args.action == "status":
        return status()
    if args.action == "stop":
        return stop()
    if args.action == "restart":
        stop()
        return start()
    return start()


if __name__ == "__main__":
    raise SystemExit(main())
