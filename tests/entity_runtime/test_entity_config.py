"""Entity-owned configuration resolution tests (S2-004-R3; task CFG-01..CFG-10).

Covers the Entity-owned configuration loader introduced by the approved
S2-004-R2 architecture decision (Alternative A).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sylphy_entity.config import (
    DEFAULT_IDENTITY_FILE,
    ENTITY_CONFIG_ENV_VAR,
    IDENTITY_ENV_VAR,
    LEGACY_IDENTITY_ENV_VAR,
    EntityConfig,
    load_entity_config,
)


VALID_IDENTITY_TOML = """[identity]
name = "SYLPHY"
identity = "A local-first AI companion with a consistent character."
personality = "Gentle and considerate."
communication_style = "Warm and thoughtful."
relationship = "Develops through interaction."
boundaries = "Respect privacy and permissions."
version = "1.0"
"""


def _write_config(tmp_path: Path, text: str) -> Path:
    config_file = tmp_path / "config.toml"
    config_file.write_text(text, encoding="utf-8")
    return config_file


def _clear_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(IDENTITY_ENV_VAR, raising=False)
    monkeypatch.delenv(LEGACY_IDENTITY_ENV_VAR, raising=False)


def test_cfg01_default_configuration_path_and_default_identity(tmp_path, monkeypatch):
    """CFG-01: Default configuration path yields the default identity file."""
    monkeypatch.delenv(ENTITY_CONFIG_ENV_VAR, raising=False)
    monkeypatch.delenv(IDENTITY_ENV_VAR, raising=False)
    monkeypatch.delenv(LEGACY_IDENTITY_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    config_file = _write_config(tmp_path, '[sylphy]\ndata_dir = "state"\n')
    resolved = load_entity_config()
    assert isinstance(resolved, EntityConfig)
    assert resolved.config_file == config_file.resolve()
    assert resolved.identity_file == Path(DEFAULT_IDENTITY_FILE)


def test_cfg02_canonical_identity_env_var_overrides_config_file(tmp_path, monkeypatch):
    """CFG-02: Canonical environment variable SYLPHY_IDENTITY_FILE wins over TOML."""
    monkeypatch.delenv(ENTITY_CONFIG_ENV_VAR, raising=False)
    monkeypatch.delenv(LEGACY_IDENTITY_ENV_VAR, raising=False)
    identity_file = tmp_path / "canonical-identity.toml"
    identity_file.write_text(VALID_IDENTITY_TOML, encoding="utf-8")
    config_file = _write_config(tmp_path, '[sylphy]\nidentity_file = "from-toml.toml"\n')
    monkeypatch.setenv(IDENTITY_ENV_VAR, str(identity_file))
    resolved = load_entity_config(config_file)
    assert resolved.identity_file == identity_file


def test_cfg03_legacy_identity_env_var_is_backward_compatible(tmp_path, monkeypatch):
    """CFG-03: Backward-compatible SYLPHY_ENTITY_IDENTITY_FILE is honored when canonical is unset."""
    monkeypatch.delenv(ENTITY_CONFIG_ENV_VAR, raising=False)
    monkeypatch.delenv(IDENTITY_ENV_VAR, raising=False)
    identity_file = tmp_path / "legacy-identity.toml"
    identity_file.write_text(VALID_IDENTITY_TOML, encoding="utf-8")
    config_file = _write_config(tmp_path, '[sylphy]\nidentity_file = "from-toml.toml"\n')
    monkeypatch.setenv(LEGACY_IDENTITY_ENV_VAR, str(identity_file))
    resolved = load_entity_config(config_file)
    assert resolved.identity_file == identity_file


def test_cfg04_canonical_identity_env_var_wins_when_both_set(tmp_path, monkeypatch):
    """CFG-04: Canonical variable wins when both Identity environment variables are set."""
    monkeypatch.delenv(ENTITY_CONFIG_ENV_VAR, raising=False)
    monkeypatch.delenv(IDENTITY_ENV_VAR, raising=False)
    monkeypatch.delenv(LEGACY_IDENTITY_ENV_VAR, raising=False)
    canonical_file = tmp_path / "canonical-identity.toml"
    canonical_file.write_text(VALID_IDENTITY_TOML, encoding="utf-8")
    legacy_file = tmp_path / "legacy-identity.toml"
    legacy_file.write_text(VALID_IDENTITY_TOML, encoding="utf-8")
    config_file = _write_config(tmp_path, '[sylphy]\nidentity_file = "from-toml.toml"\n')
    monkeypatch.setenv(IDENTITY_ENV_VAR, str(canonical_file))
    monkeypatch.setenv(LEGACY_IDENTITY_ENV_VAR, str(legacy_file))
    resolved = load_entity_config(config_file)
    assert resolved.identity_file == canonical_file


def test_cfg05_top_level_toml_identity_file_is_used(tmp_path, monkeypatch):
    """CFG-05: Top-level TOML identity_file takes effect over defaults."""
    monkeypatch.delenv(ENTITY_CONFIG_ENV_VAR, raising=False)
    monkeypatch.delenv(IDENTITY_ENV_VAR, raising=False)
    monkeypatch.delenv(LEGACY_IDENTITY_ENV_VAR, raising=False)
    config_file = _write_config(tmp_path, 'identity_file = "top-level-identity.toml"\n')
    resolved = load_entity_config(config_file)
    assert resolved.identity_file == Path("top-level-identity.toml")


def test_cfg06_ollama_table_identity_file_is_fallback(tmp_path, monkeypatch):
    """CFG-06: [ollama].identity_file is used when no higher-precedence source is set."""
    monkeypatch.delenv(ENTITY_CONFIG_ENV_VAR, raising=False)
    monkeypatch.delenv(IDENTITY_ENV_VAR, raising=False)
    monkeypatch.delenv(LEGACY_IDENTITY_ENV_VAR, raising=False)
    config_file = _write_config(tmp_path, '[ollama]\nidentity_file = "ollama-identity.toml"\n')
    resolved = load_entity_config(config_file)
    assert resolved.identity_file == Path("ollama-identity.toml")


def test_cfg07_precedence_among_multiple_sources(tmp_path, monkeypatch):
    """CFG-07: Precedence holds when multiple sources could provide an Identity path."""
    monkeypatch.delenv(ENTITY_CONFIG_ENV_VAR, raising=False)
    monkeypatch.delenv(IDENTITY_ENV_VAR, raising=False)
    monkeypatch.delenv(LEGACY_IDENTITY_ENV_VAR, raising=False)
    config_file = _write_config(
        tmp_path,
        'identity_file = "top-level.toml"\n[ollama]\nidentity_file = "ollama.toml"\n',
    )
    # Top-level TOML beats [ollama] table.
    assert load_entity_config(config_file).identity_file == Path("top-level.toml")
    # Backward-compatible env var beats TOML when canonical is unset.
    monkeypatch.setenv(LEGACY_IDENTITY_ENV_VAR, "legacy.toml")
    assert load_entity_config(config_file).identity_file == Path("legacy.toml")


def test_cfg08_relative_identity_path_semantics_are_preserved(tmp_path, monkeypatch):
    """CFG-08: Resolved Identity paths stay relative to the process CWD."""
    monkeypatch.delenv(ENTITY_CONFIG_ENV_VAR, raising=False)
    monkeypatch.delenv(IDENTITY_ENV_VAR, raising=False)
    monkeypatch.delenv(LEGACY_IDENTITY_ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    config_file = _write_config(tmp_path, 'identity_file = "nested/identity.toml"\n')
    resolved = load_entity_config(config_file)
    assert resolved.identity_file == Path("nested/identity.toml")
    assert not resolved.identity_file.is_absolute()


def test_cfg09_missing_configuration_file_is_explicit(tmp_path, monkeypatch):
    """CFG-09: A missing configuration file fails explicitly instead of silently defaulting."""
    monkeypatch.delenv(IDENTITY_ENV_VAR, raising=False)
    monkeypatch.delenv(LEGACY_IDENTITY_ENV_VAR, raising=False)
    monkeypatch.setenv(ENTITY_CONFIG_ENV_VAR, str(tmp_path / "missing" / "config.toml"))
    with pytest.raises(FileNotFoundError):
        load_entity_config()


def test_cfg10_sylphy_config_env_var_selects_configuration_file(tmp_path, monkeypatch):
    """CFG-10: SYLPHY_CONFIG selects the Entity configuration file."""
    monkeypatch.delenv(IDENTITY_ENV_VAR, raising=False)
    monkeypatch.delenv(LEGACY_IDENTITY_ENV_VAR, raising=False)
    config_file = _write_config(tmp_path, 'identity_file = "from-sylphy-config.toml"\n')
    monkeypatch.setenv(ENTITY_CONFIG_ENV_VAR, str(config_file))
    resolved = load_entity_config()
    assert resolved.config_file == config_file.resolve()
    assert resolved.identity_file == Path("from-sylphy-config.toml")
