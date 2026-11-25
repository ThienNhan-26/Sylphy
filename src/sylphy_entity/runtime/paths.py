"""Per-user runtime paths shared by the daemon and its client CLI."""

from __future__ import annotations

import os
from pathlib import Path


def default_runtime_dir() -> Path:
    if os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return root / "SYLPHY" / "EntityRuntime"
    return Path.home() / ".local" / "state" / "sylphy-entity"
