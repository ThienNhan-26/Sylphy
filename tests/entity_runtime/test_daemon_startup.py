"""Entity daemon startup wiring tests (S2-004-R3; task START-01..START-07).

Verifies that the production daemon startup path resolves Entity-owned
configuration, reaches supervisor construction, and loads Identity through
the Entity-owned loader — without touching Legacy configuration modules.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from sylphy_entity import daemon as daemon_module
from sylphy_entity.config import EntityConfig, load_entity_config
from sylphy_entity.identity import IdentityConfig, load_identity
from sylphy_entity.runtime.supervisor import EntitySupervisor


VALID_IDENTITY_TOML = """[identity]
name = "SYLPHY"
identity = "A local-first AI companion with a consistent character."
personality = "Gentle and considerate."
communication_style = "Warm and thoughtful."
relationship = "Develops through interaction."
boundaries = "Respect privacy and permissions."
version = "1.0"
"""


class RecordingEntitySupervisor:
    """Minimal stand-in for EntitySupervisor used to observe daemon wiring."""

    instances: list = []

    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs
        RecordingEntitySupervisor.instances.append(self)

    async def start(self) -> None:
        return None

    async def serve_forever(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_supervisor_recorder() -> None:
    RecordingEntitySupervisor.instances.clear()
    yield
    RecordingEntitySupervisor.instances.clear()


def _run_daemon_startup(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, config_text: str) -> list:
    """Run the real daemon main() startup path with a recording supervisor."""
    monkeypatch.delenv("SYLPHY_CONFIG", raising=False)
    monkeypatch.delenv("SYLPHY_IDENTITY_FILE", raising=False)
    monkeypatch.delenv("SYLPHY_ENTITY_IDENTITY_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text(config_text, encoding="utf-8")
    monkeypatch.setattr(daemon_module, "EntitySupervisor", RecordingEntitySupervisor)
    monkeypatch.setattr(sys, "argv", ["sylphy-entity", "--port", "0"])
    daemon_module.main()  # must not raise NameError: the config loader is bound
    return RecordingEntitySupervisor.instances


def test_start01_daemon_resolves_entity_owned_configuration_loader(monkeypatch, tmp_path):
    """START-01: The Entity daemon resolves its own Entity-owned configuration loader."""
    monkeypatch.delenv("SYLPHY_CONFIG", raising=False)
    monkeypatch.delenv("SYLPHY_IDENTITY_FILE", raising=False)
    monkeypatch.delenv("SYLPHY_ENTITY_IDENTITY_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.toml").write_text('[sylphy]\ndata_dir = "state"\n', encoding="utf-8")
    resolved = daemon_module.load_entity_config()
    assert isinstance(resolved, EntityConfig)
    assert daemon_module.load_entity_config.__module__ == "sylphy_entity.config"


def test_start02_daemon_main_does_not_raise_previous_name_error(monkeypatch, tmp_path):
    """START-02: daemon.main() executes past configuration loading without NameError."""
    instances = _run_daemon_startup(monkeypatch, tmp_path, 'identity_file = "identity.toml"\n')
    assert len(instances) == 1


def test_start03_startup_reaches_entity_supervisor_construction(monkeypatch, tmp_path):
    """START-03: Startup reaches EntitySupervisor construction."""
    instances = _run_daemon_startup(monkeypatch, tmp_path, 'identity_file = "identity.toml"\n')
    assert len(instances) == 1
    assert instances[0].kwargs["port"] == 0


def test_start04_resolved_identity_path_reaches_supervisor(monkeypatch, tmp_path):
    """START-04: Resolved Identity path from Entity config reaches the EntitySupervisor."""
    identity_name = "custom-identity.toml"
    (tmp_path / identity_name).write_text(VALID_IDENTITY_TOML, encoding="utf-8")
    instances = _run_daemon_startup(
        monkeypatch, tmp_path, f'identity_file = "{identity_name}"\n'
    )
    assert len(instances) == 1
    assert instances[0].kwargs["identity_file"] == Path(identity_name)


def test_start05_identity_loading_occurs_through_entity_owned_loader(monkeypatch, tmp_path):
    """START-05: Identity loading is performed through the Entity-owned loader."""
    captured: dict = {}

    def fake_load_identity(identity_path=None, **kwargs):
        captured["identity_path"] = identity_path
        captured["fallback_default"] = kwargs.get("fallback_default")
        return IdentityConfig(
            name="SYLPHY",
            identity="A local-first AI companion.",
            personality="Gentle and considerate.",
            communication_style="Warm and thoughtful.",
            relationship="Develops through interaction.",
            boundaries="Respect privacy and permissions.",
            version="1.0",
        )

    monkeypatch.setattr("sylphy_entity.identity.load_identity", fake_load_identity)
    supervisor = EntitySupervisor(lock_path=tmp_path / "entity.lock", port=0)
    assert captured["identity_path"] == "identity.toml"
    assert captured["fallback_default"] is True


def test_start06_missing_and_invalid_identity_behavior_matches_contract(monkeypatch, tmp_path):
    """START-06: Missing Identity falls back explicitly; invalid Identity raises explicitly."""
    monkeypatch.delenv("SYLPHY_CONFIG", raising=False)
    monkeypatch.delenv("SYLPHY_ENTITY_IDENTITY_FILE", raising=False)
    (tmp_path / "config.toml").write_text('[sylphy]\ndata_dir = "state"\n', encoding="utf-8")
    monkeypatch.setenv("SYLPHY_IDENTITY_FILE", str(tmp_path / "missing.toml"))
    resolved = load_entity_config(tmp_path / "config.toml")
    assert resolved.identity_file == tmp_path / "missing.toml"
    identity = load_identity(identity_path=resolved.identity_file, fallback_default=True)
    assert identity.name == "Sylphy"  # deterministic fallback

    invalid_file = tmp_path / "invalid.toml"
    invalid_file.write_text('[identity]\nname = "SYLPHY"\n', encoding="utf-8")
    monkeypatch.setenv("SYLPHY_IDENTITY_FILE", str(invalid_file))
    resolved_invalid = load_entity_config(tmp_path / "config.toml")
    with pytest.raises(ValueError, match="Missing or empty identity fields"):
        load_identity(identity_path=resolved_invalid.identity_file, fallback_default=True)


def test_start07_entity_startup_does_not_import_legacy_configuration_modules(monkeypatch, tmp_path):
    """START-07: Entity startup path avoids importing Legacy configuration modules."""
    saved = {name: sys.modules.pop(name, None) for name in ("sylphy.config", "sylphy.identity")}
    try:
        instances = _run_daemon_startup(monkeypatch, tmp_path, 'identity_file = "identity.toml"\n')
        assert len(instances) == 1
        assert "sylphy.config" not in sys.modules
        assert "sylphy.identity" not in sys.modules
    finally:
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module
