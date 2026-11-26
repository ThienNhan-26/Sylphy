"""Entity-owned runtime configuration for the SYLPHY Entity daemon.

This module is owned by the ``sylphy_entity`` package and intentionally does
not import the Legacy ``sylphy.config`` module (see
``docs/rebuild/ADR-S2-004_R2.md``).

It exposes the minimal configuration contract required by Entity startup:
the resolved Identity file path, plus the configuration file it was loaded
from.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

ENTITY_CONFIG_ENV_VAR = "SYLPHY_CONFIG"
IDENTITY_ENV_VAR = "SYLPHY_IDENTITY_FILE"
LEGACY_IDENTITY_ENV_VAR = "SYLPHY_ENTITY_IDENTITY_FILE"
DEFAULT_CONFIG_FILE = "config.toml"
DEFAULT_IDENTITY_FILE = "identity.toml"


@dataclass(frozen=True)
class EntityConfig:
    """Minimal Entity-owned runtime configuration required by the daemon."""

    identity_file: Path
    config_file: Path


def load_entity_config(path: Path | None = None) -> EntityConfig:
    """Load the Entity-owned runtime configuration.

    The Identity path is resolved with the Entity-owned precedence:

    1. ``SYLPHY_IDENTITY_FILE`` (canonical Identity environment variable)
    2. ``SYLPHY_ENTITY_IDENTITY_FILE`` (backward-compatible alias)
    3. top-level ``identity_file`` key in the Entity configuration file
    4. ``identity_file`` key in the ``[ollama]`` table of the Entity configuration file
    5. default ``identity.toml``

    When both Identity environment variables are set, the canonical variable
    (``SYLPHY_IDENTITY_FILE``) wins.

    Args:
        path: Explicit configuration file path; takes precedence over the
            ``SYLPHY_CONFIG`` environment variable. Defaults to ``SYLPHY_CONFIG``
            or ``config.toml`` relative to the process working directory.

    Returns:
        EntityConfig with the resolved Identity path and the loaded config path.

    Raises:
        FileNotFoundError: If the resolved configuration file does not exist.
        tomllib.TOMLDecodeError: If the configuration file is not valid TOML.
    """
    config_path = Path(path) if path is not None else Path(
        os.environ.get(ENTITY_CONFIG_ENV_VAR, DEFAULT_CONFIG_FILE)
    )
    config_path = config_path.resolve()
    with config_path.open("rb") as file:
        raw = tomllib.load(file)
    identity_file_value = _resolve_identity_file(raw)
    return EntityConfig(identity_file=Path(identity_file_value), config_file=config_path)


def _resolve_identity_file(raw: dict) -> str:
    """Resolve the Identity file path from environment variables and TOML."""
    canonical = os.environ.get(IDENTITY_ENV_VAR)
    if canonical is not None and canonical.strip():
        return canonical
    legacy = os.environ.get(LEGACY_IDENTITY_ENV_VAR)
    if legacy is not None and legacy.strip():
        return legacy
    top_level = raw.get("identity_file")
    if isinstance(top_level, str) and top_level.strip():
        return top_level
    fallback = DEFAULT_IDENTITY_FILE
    ollama_table = raw.get("ollama")
    if isinstance(ollama_table, dict):
        candidate = ollama_table.get("identity_file", fallback)
        if isinstance(candidate, str) and candidate.strip():
            fallback = candidate
    return fallback
