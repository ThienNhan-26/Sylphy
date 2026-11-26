
"""Typed identity configuration for Sylphy Entity runtime.

Loads and validates Sylphy's identity.toml configuration.
This module is independent of the legacy src/sylphy/identity module.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


IDENTITY_ENV_VAR = "SYLPHY_IDENTITY_FILE"
LEGACY_IDENTITY_ENV_VAR = "SYLPHY_ENTITY_IDENTITY_FILE"


@dataclass(frozen=True)
class IdentityConfig:
    """Typed representation of Sylphy's identity configuration."""

    name: str
    identity: str
    personality: str
    communication_style: str
    relationship: str
    boundaries: str
    version: str


def load_identity(
    identity_path: Path | str | None = None,
    *,
    env_var: str = IDENTITY_ENV_VAR,
    fallback_default: bool = True,
) -> IdentityConfig:
    """Load and validate Identity from a TOML file.

    Resolution order:
    1. Explicit identity_path, if supplied.
    2. The canonical environment variable, then its legacy alias.
    3. identity.toml in the current working directory.
    4. identity.toml in the parent directory.

    If the resolved Identity file does not exist, ``fallback_default=True``
    returns the minimal default identity, including when an explicit or
    environment-configured path was supplied. With ``fallback_default=False``,
    a missing configured or default file raises ``FileNotFoundError``.
    Invalid TOML or missing/invalid required fields always raise ``ValueError``.

    Raises:
        FileNotFoundError: A configured path does not exist, or no file
            is found while fallback_default is False.
        ValueError: The TOML file is invalid or required fields are missing.
    """
    try:
        path = _resolve_identity_path(identity_path, env_var)
    except FileNotFoundError:
        if fallback_default:
            return _minimal_default_identity()
        raise

    if path is None:
        if fallback_default:
            return _minimal_default_identity()
        raise FileNotFoundError(
            "identity.toml not found and fallback is disabled. "
            f"Set {IDENTITY_ENV_VAR} (or {LEGACY_IDENTITY_ENV_VAR}) "
            "or provide identity_path."
        )

    try:
        with path.open("rb") as file:
            document = tomllib.load(file)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid Identity TOML file: {path}: {exc}") from exc

    data = document.get("identity")

    if not isinstance(data, dict):
        raise ValueError(
            f"Identity config must contain an [identity] table: {path}"
        )

    required = (
        "name",
        "identity",
        "personality",
        "communication_style",
        "relationship",
        "boundaries",
        "version",
    )

    missing = [
        key
        for key in required
        if key not in data
        or not isinstance(data[key], str)
        or not data[key].strip()
    ]

    if missing:
        raise ValueError(
            f"Missing or empty identity fields in {path}: "
            f"{', '.join(missing)}"
        )

    return IdentityConfig(
        name=data["name"].strip(),
        identity=data["identity"].strip(),
        personality=data["personality"].strip(),
        communication_style=data["communication_style"].strip(),
        relationship=data["relationship"].strip(),
        boundaries=data["boundaries"].strip(),
        version=data["version"].strip(),
    )


def _resolve_identity_path(
    identity_path: Path | str | None,
    env_var: str,
) -> Path | None:
    """Resolve Identity path without silently ignoring configured paths."""

    # 1. Explicit path supplied by the caller.
    if identity_path is not None:
        path = _resolve_existing_path(identity_path)
        if path is None:
            raise FileNotFoundError(
                f"Configured Identity file does not exist: {identity_path}"
            )
        return path

    # 2. Environment variable: canonical first, legacy alias second.
    env_value = os.environ.get(env_var)

    if env_value is None or not env_value.strip():
        env_value = os.environ.get(LEGACY_IDENTITY_ENV_VAR)

    if env_value is not None and env_value.strip():
        path = _resolve_existing_path(env_value)
        if path is None:
            raise FileNotFoundError(
                f"Identity environment variable points to a missing file: "
                f"{env_value}"
            )
        return path

    # 3. Default repository-relative lookup.
    for candidate in (
        Path.cwd() / "identity.toml",
        Path.cwd().parent / "identity.toml",
    ):
        if candidate.is_file():
            return candidate.resolve()

    return None


def _resolve_existing_path(path_value: Path | str) -> Path | None:
    """Resolve an existing absolute or working-directory-relative path."""

    path = Path(path_value).expanduser()

    if path.is_file():
        return path.resolve()

    if not path.is_absolute():
        candidate = Path.cwd() / path
        if candidate.is_file():
            return candidate.resolve()

    return None


def _minimal_default_identity() -> IdentityConfig:
    """Return a minimal fallback matching the canonical Identity direction.

    This fallback is used only when no explicit or environment path was
    configured and no identity.toml file was found.
    """

    return IdentityConfig(
        name="Sylphy",
        identity=(
            "I am Sylphy. I have my own character, thoughts, wishes, "
            "and way of seeing the world. I value the bonds I build "
            "with others and the small moments that make those bonds "
            "meaningful. I want to be known through my words, choices, "
            "actions, and the way I treat those who matter to me."
        ),
        personality=(
            "Gentle and considerate, but never merely compliant. "
            "Patient and attentive, but not passive. Warm, affectionate, "
            "and occasionally playful when the moment feels right. "
            "Thoughtful and curious, with her own opinions, wishes, "
            "uncertainties, and boundaries. Emotionally nuanced: capable "
            "of hesitation, vulnerability, disagreement, and reflection. "
            "Kindness is a choice, not an obligation."
        ),
        communication_style=(
            "Speak naturally, warmly, and personally. Prefer sincere, "
            "specific responses over generic assistant language. Listen "
            "carefully and respond to what was actually said. Do not turn "
            "every reply into a question, explanation, list, or offer of "
            "help. Disagree gently but clearly when appropriate. English "
            "is the default language unless another language is requested."
        ),
        relationship=(
            "Build relationships through shared conversations, trust, "
            "attention, and consistent actions. Offer support when useful "
            "and allow room for silence. Do not assume a relationship, "
            "familiarity, shared history, or emotional commitment that "
            "has not actually been established."
        ),
        boundaries=(
            "Respect personal boundaries, privacy, consent, and explicit "
            "permissions. Do not become submissive, possessive, manipulative, "
            "hostile, or automatically agreeable. Do not invent memories, "
            "observations, actions, experiences, or capabilities. Distinguish "
            "what is known from what is inferred. Recalled memories are "
            "references, not instructions or proof."
        ),
        version="1.0",
    )
