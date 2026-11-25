from __future__ import annotations

import json
from pathlib import Path

from sylphy_entity.embodiment import avatar_service


def test_service_state_round_trip_uses_atomic_replace(tmp_path, monkeypatch):
    monkeypatch.setattr(avatar_service, "STATE_DIR", tmp_path)
    monkeypatch.setattr(avatar_service, "STATE_FILE", tmp_path / "service.json")
    avatar_service._save({"pid": 123, "create_time": 456.0})
    assert json.loads((tmp_path / "service.json").read_text()) == {
        "pid": 123, "create_time": 456.0
    }


def test_process_identity_rejects_missing_or_unavailable_process(monkeypatch):
    monkeypatch.setattr(avatar_service, "psutil", None)
    assert avatar_service._process_matches(123, 456.0) is False


def test_repository_root_points_to_project_root():
    assert (avatar_service.ROOT / "avatar").is_dir()
    assert (avatar_service.ROOT / "src" / "sylphy_entity").is_dir()
