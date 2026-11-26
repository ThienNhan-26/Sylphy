"""Explicit, local TOML configuration."""

from dataclasses import dataclass
import ipaddress
import math
import os
from pathlib import Path
from urllib.parse import urlsplit

from .avatar import AvatarConfig
from .camera import VisionConfig
try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib  # type: ignore[no-redef]


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    log_dir: Path
    database_name: str
    ollama_host: str
    ollama_model: str
    ollama_timeout_seconds: float
    context_budget: int
    system_instructions: str
    identity_file: Path
    avatar: AvatarConfig
    vision: VisionConfig
    memory_enabled: bool
    memory_context_budget: int
    memory_max_items: int
    heartbeat_enabled: bool
    heartbeat_interval_seconds: float
    max_concurrent_generations: int
    cognition_enabled: bool
    cognition_max_candidates: int
    cognition_max_context_messages: int
    cognition_max_pending_tasks: int
    cognition_shutdown_grace_seconds: float
    presence_enabled: bool
    presence_queue_capacity: int
    companion_enabled: bool
    autonomy_enabled: bool
    initiative_enabled: bool
    exploration_enabled: bool
    exploration_max_queue_items: int
    exploration_cooldown_seconds: float
    exploration_max_attempts_per_period: int
    exploration_period_seconds: float
    exploration_question_max_length: int
    exploration_context_max_chars: int
    exploration_question_expiry_days: int
    investigation_enabled: bool
    investigation_max_attempts: int
    investigation_max_duration_seconds: int
    investigation_max_model_calls: int
    investigation_max_search_requests: int
    investigation_max_pages: int
    investigation_max_source_bytes: int
    investigation_max_extracted_text_chars: int
    web_search_enabled: bool
    web_search_provider: str
    web_search_timeout_seconds: float
    web_search_max_results: int
    web_search_max_response_bytes: int
    web_reader_enabled: bool
    web_reader_connect_timeout_seconds: float
    web_reader_read_timeout_seconds: float
    web_reader_total_timeout_seconds: float
    web_reader_max_redirects: int
    web_reader_max_response_bytes: int
    web_reader_max_extracted_chars: int
    web_reader_max_concurrent_requests: int
    voice: "VoiceConfig"

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_name


def load_config(path: Path | None = None) -> AppConfig:
    """Load TOML config; SYLPHY_CONFIG and OLLAMA_* env vars may override it."""
    config_path = path or Path(os.environ.get("SYLPHY_CONFIG", "config.toml"))
    config_path = config_path.resolve()
    with config_path.open("rb") as file:
        raw = tomllib.load(file)
    app = raw.get("sylphy", {})
    ollama = raw.get("ollama", {})
    memory = raw.get("memory", {})
    runtime = raw.get("runtime", {})
    resources = raw.get("resources", {})
    cognition = raw.get("cognition", {})
    presence = raw.get("presence", {})
    exploration = raw.get("exploration", {})
    companion = raw.get("companion", {})
    investigation = raw.get("investigation", {})
    web_search = raw.get("web_search", {})
    web_reader = raw.get("web_reader", {})
    vision_raw = raw.get("vision", {})
    voice_raw = raw.get("voice", {})
    avatar = _avatar_config(raw.get("avatar", {}), config_path.parent)
    heartbeat_interval = float(runtime.get("heartbeat_interval_seconds", 30))
    max_generations = int(resources.get("max_concurrent_generations", 1))
    heartbeat_enabled = runtime.get("heartbeat_enabled", True)
    if not isinstance(heartbeat_enabled, bool):
        raise ValueError("runtime.heartbeat_enabled must be a boolean")
    if heartbeat_interval <= 0:
        raise ValueError("runtime.heartbeat_interval_seconds must be positive")
    if max_generations != 1:
        raise ValueError("resources.max_concurrent_generations must be 1; concurrent conversations are not supported")
    cognition_max_pending = int(cognition.get("max_pending_tasks", 100))
    cognition_shutdown_grace = float(cognition.get("shutdown_grace_seconds", 5))
    presence_enabled = presence.get("enabled", False)
    presence_capacity = int(presence.get("queue_capacity", 20))
    companion_enabled = companion.get("enabled", False)
    if not isinstance(companion_enabled, bool):
        raise ValueError("companion.enabled must be a boolean")
    autonomy = raw.get("autonomy", {})
    autonomy_enabled = autonomy.get("enabled", False)
    if not isinstance(autonomy_enabled, bool):
        raise ValueError("autonomy.enabled must be a boolean")
    initiative = raw.get("initiative", {})
    initiative_enabled = initiative.get("enabled", False)
    if not isinstance(initiative_enabled, bool):
        raise ValueError("initiative.enabled must be a boolean")
    exploration_enabled = exploration.get("enabled", False)
    exploration_queue = exploration.get("max_queue_items", 20)
    exploration_cooldown = exploration.get("cooldown_seconds", 3600)
    exploration_attempts = exploration.get("max_attempts_per_period", 3)
    exploration_period = exploration.get("period_seconds", 86400)
    exploration_question_length = exploration.get("question_max_length", 400)
    exploration_context_chars = exploration.get("context_max_chars", 8000)
    exploration_expiry_days = exploration.get("question_expiry_days", 30)
    investigation_enabled = investigation.get("enabled", False)
    investigation_limits = {
        "max_attempts": investigation.get("max_attempts", 3),
        "max_duration_seconds": investigation.get("max_duration_seconds", 900),
        "max_model_calls": investigation.get("max_model_calls", 3),
        "max_search_requests": investigation.get("max_search_requests", 3),
        "max_pages": investigation.get("max_pages", 5),
        "max_source_bytes": investigation.get("max_source_bytes", 2_000_000),
        "max_extracted_text_chars": investigation.get("max_extracted_text_chars", 12_000),
    }
    search_enabled = web_search.get("enabled", False)
    search_provider = web_search.get("provider", "brave")
    search_timeout = web_search.get("timeout_seconds", 10)
    search_max_results = web_search.get("max_results", 5)
    search_max_bytes = web_search.get("max_response_bytes", 512_000)
    reader_enabled = web_reader.get("enabled", False)
    reader_connect_timeout = web_reader.get("connect_timeout_seconds", 5)
    reader_read_timeout = web_reader.get("read_timeout_seconds", 5)
    reader_total_timeout = web_reader.get("total_timeout_seconds", 15)
    reader_redirects = web_reader.get("max_redirects", 3)
    reader_max_bytes = web_reader.get("max_response_bytes", 1_048_576)
    reader_max_chars = web_reader.get("max_extracted_chars", 20_000)
    reader_concurrency = web_reader.get("max_concurrent_requests", 1)
    ollama_host = os.environ.get("OLLAMA_HOST", ollama.get("host", "http://127.0.0.1:11434"))
    if isinstance(ollama_host, str) and "://" not in ollama_host:
        ollama_host = "http://" + ollama_host
    vision = _vision_config(vision_raw)
    voice = _voice_config(voice_raw, config_path.parent, Path(app.get("data_dir", "data")))
    if vision.enabled:
        _validate_local_ollama_host(ollama_host, "Vision")
    if cognition_max_pending < 1:
        raise ValueError("cognition.max_pending_tasks must be positive")
    if cognition_shutdown_grace <= 0:
        raise ValueError("cognition.shutdown_grace_seconds must be positive")
    if not isinstance(presence_enabled, bool):
        raise ValueError("presence.enabled must be a boolean")
    if presence_capacity < 1:
        raise ValueError("presence.queue_capacity must be positive")
    if not isinstance(exploration_enabled, bool):
        raise ValueError("exploration.enabled must be a boolean")
    if exploration_enabled:
        if not isinstance(ollama_host, str) or not ollama_host or ollama_host.strip() != ollama_host:
            raise ValueError("Exploration requires a valid unauthenticated loopback Ollama host")
        try:
            parsed_host = urlsplit(ollama_host)
            hostname = parsed_host.hostname or ""
            port = parsed_host.port
        except ValueError as exc:
            raise ValueError("Exploration requires a valid unauthenticated loopback Ollama host") from exc
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = hostname.casefold() == "localhost"
        authority = parsed_host.netloc.rsplit("@", 1)[-1]
        malformed_authority = authority.endswith(":")
        invalid_port = port is not None and not 1 <= port <= 65535
        malformed_url = (not parsed_host.netloc or not hostname or parsed_host.path not in {"", "/"}
                         or parsed_host.query or parsed_host.fragment or "?" in ollama_host
                         or "#" in ollama_host or malformed_authority)
        if (parsed_host.scheme not in {"http", "https"} or not is_loopback
                or parsed_host.username is not None or parsed_host.password is not None
                or malformed_url or invalid_port):
            raise ValueError("Exploration requires a valid unauthenticated loopback Ollama host")
    if not isinstance(investigation_enabled, bool):
        raise ValueError("investigation.enabled must be a boolean")
    investigation_maxima = {
        "max_attempts": 10,
        "max_duration_seconds": 86_400,
        "max_model_calls": 100,
        "max_search_requests": 100,
        "max_pages": 100,
        "max_source_bytes": 50_000_000,
        "max_extracted_text_chars": 1_000_000,
    }
    for name, value in investigation_limits.items():
        maximum = investigation_maxima[name]
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"investigation.{name} must be an integer between 1 and {maximum}")
    if not isinstance(search_enabled, bool) or not isinstance(reader_enabled, bool):
        raise ValueError("web_search.enabled and web_reader.enabled must be booleans")
    if search_provider != "brave":
        raise ValueError("web_search.provider must be 'brave'")
    for name, value, maximum in (
        ("web_search.timeout_seconds", search_timeout, 60),
        ("web_reader.connect_timeout_seconds", reader_connect_timeout, 120),
        ("web_reader.read_timeout_seconds", reader_read_timeout, 120),
        ("web_reader.total_timeout_seconds", reader_total_timeout, 120),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value <= maximum:
            raise ValueError(f"{name} must be greater than zero and at most {maximum}")
    for name, value, maximum in (
        ("web_search.max_results", search_max_results, 10),
        ("web_search.max_response_bytes", search_max_bytes, 512_000),
        ("web_reader.max_redirects", reader_redirects, 5),
        ("web_reader.max_response_bytes", reader_max_bytes, 10_000_000),
        ("web_reader.max_extracted_chars", reader_max_chars, 1_000_000),
        ("web_reader.max_concurrent_requests", reader_concurrency, 4),
    ):
        minimum = 0 if name.endswith("max_redirects") else 1
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
    for name, value in (("max_queue_items", exploration_queue),
                        ("max_attempts_per_period", exploration_attempts),
                        ("question_max_length", exploration_question_length),
                        ("context_max_chars", exploration_context_chars),
                        ("question_expiry_days", exploration_expiry_days)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"exploration.{name} must be an integer")
    for name, value in (("cooldown_seconds", exploration_cooldown), ("period_seconds", exploration_period)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"exploration.{name} must be a finite number")
    exploration_cooldown = float(exploration_cooldown)
    exploration_period = float(exploration_period)
    if exploration_queue < 1:
        raise ValueError("exploration.max_queue_items must be positive")
    if exploration_cooldown <= 0:
        raise ValueError("exploration.cooldown_seconds must be positive")
    if exploration_attempts < 1:
        raise ValueError("exploration.max_attempts_per_period must be positive")
    if exploration_period < exploration_cooldown:
        raise ValueError("exploration.period_seconds must be at least cooldown_seconds")
    if exploration_question_length < 1 or exploration_context_chars < 1 or exploration_expiry_days < 1:
        raise ValueError("exploration length, context, and expiry limits must be positive")
    return AppConfig(
        data_dir=Path(app.get("data_dir", "data")),
        log_dir=Path(app.get("log_dir", "logs")),
        database_name=app.get("database_name", "sylphy.sqlite3"),
        ollama_host=ollama_host,
        ollama_model=os.environ.get("OLLAMA_MODEL", ollama.get("model", "")),
        ollama_timeout_seconds=float(ollama.get("timeout_seconds", 120)),
        context_budget=int(os.environ.get("SYLPHY_CONTEXT_BUDGET", ollama.get("context_budget", 8192))),
        system_instructions=ollama.get("system_instructions", "You are a helpful local assistant."),
        identity_file=Path(os.environ.get("SYLPHY_IDENTITY_FILE", raw.get("identity_file", ollama.get("identity_file", "identity.toml")))),
        avatar=avatar,
        vision=vision,
        memory_enabled=memory.get("enabled", True),
        memory_context_budget=int(memory.get("context_budget", 512)),
        memory_max_items=int(memory.get("max_items", 5)),
        heartbeat_enabled=heartbeat_enabled,
        heartbeat_interval_seconds=heartbeat_interval,
        max_concurrent_generations=max_generations,
        cognition_enabled=cognition.get("enabled", False),
        cognition_max_candidates=int(cognition.get("max_candidates_per_turn", 3)),
        cognition_max_context_messages=int(cognition.get("max_context_messages", 6)),
        cognition_max_pending_tasks=cognition_max_pending,
        cognition_shutdown_grace_seconds=cognition_shutdown_grace,
        presence_enabled=presence_enabled,
        presence_queue_capacity=presence_capacity,
        companion_enabled=companion_enabled,
        autonomy_enabled=autonomy_enabled,
        initiative_enabled=initiative_enabled,
        exploration_enabled=exploration_enabled,
        exploration_max_queue_items=exploration_queue,
        exploration_cooldown_seconds=exploration_cooldown,
        exploration_max_attempts_per_period=exploration_attempts,
        exploration_period_seconds=exploration_period,
        exploration_question_max_length=exploration_question_length,
        exploration_context_max_chars=exploration_context_chars,
        exploration_question_expiry_days=exploration_expiry_days,
        investigation_enabled=investigation_enabled,
        investigation_max_attempts=investigation_limits["max_attempts"],
        investigation_max_duration_seconds=investigation_limits["max_duration_seconds"],
        investigation_max_model_calls=investigation_limits["max_model_calls"],
        investigation_max_search_requests=investigation_limits["max_search_requests"],
        investigation_max_pages=investigation_limits["max_pages"],
        investigation_max_source_bytes=investigation_limits["max_source_bytes"],
        investigation_max_extracted_text_chars=investigation_limits["max_extracted_text_chars"],
        web_search_enabled=search_enabled,
        web_search_provider=search_provider,
        web_search_timeout_seconds=float(search_timeout),
        web_search_max_results=search_max_results,
        web_search_max_response_bytes=search_max_bytes,
        web_reader_enabled=reader_enabled,
        web_reader_connect_timeout_seconds=float(reader_connect_timeout),
        web_reader_read_timeout_seconds=float(reader_read_timeout),
        web_reader_total_timeout_seconds=float(reader_total_timeout),
        web_reader_max_redirects=reader_redirects,
        web_reader_max_response_bytes=reader_max_bytes,
        web_reader_max_extracted_chars=reader_max_chars,
        web_reader_max_concurrent_requests=reader_concurrency,
        voice=voice,
    )


@dataclass(frozen=True)
class VoiceConfig:
    enabled: bool = False
    model_dir: Path = Path("data/voice-models")
    whisper_model: str = "base.en"
    tts_model: str = "kokoro-v1.0.onnx"
    tts_voices: str = "voices-v1.0.bin"
    voice: str = "af_heart"
    microphone_device: int | None = None
    sample_rate: int = 16000
    max_utterance_seconds: int = 25
    silence_seconds: float = 0.8


def _voice_config(raw: object, config_dir: Path, data_dir: Path) -> VoiceConfig:
    if not isinstance(raw, dict):
        raise ValueError("voice settings must be a TOML table")
    defaults = VoiceConfig()
    enabled = raw.get("enabled", defaults.enabled)
    if not isinstance(enabled, bool):
        raise ValueError("voice.enabled must be a boolean")
    device = raw.get("microphone_device")
    if device is not None and (isinstance(device, bool) or not isinstance(device, int) or device < 0):
        raise ValueError("voice.microphone_device must be a non-negative device index or omitted")
    rate = raw.get("sample_rate", defaults.sample_rate)
    maximum = raw.get("max_utterance_seconds", defaults.max_utterance_seconds)
    silence = raw.get("silence_seconds", defaults.silence_seconds)
    if isinstance(rate, bool) or not isinstance(rate, int) or rate != 16000:
        raise ValueError("voice.sample_rate must be 16000")
    if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 120:
        raise ValueError("voice.max_utterance_seconds must be from 1 to 120")
    if isinstance(silence, bool) or not isinstance(silence, (int, float)) or not math.isfinite(silence) or not 0.3 <= silence <= 5:
        raise ValueError("voice.silence_seconds must be from 0.3 to 5")
    model_dir_raw = raw.get("model_dir")
    if model_dir_raw is None:
        model_dir = data_dir if data_dir.is_absolute() else config_dir / data_dir
        model_dir = model_dir / "voice-models"
    else:
        if not isinstance(model_dir_raw, str) or not model_dir_raw:
            raise ValueError("voice.model_dir must be a non-empty path")
        model_dir = Path(model_dir_raw)
        if not model_dir.is_absolute():
            model_dir = config_dir / model_dir
    whisper = raw.get("whisper_model", defaults.whisper_model)
    tts_model = raw.get("tts_model", defaults.tts_model)
    tts_voices = raw.get("tts_voices", defaults.tts_voices)
    voice_name = raw.get("voice", defaults.voice)
    for key, value in (("whisper_model", whisper), ("tts_model", tts_model),
                       ("tts_voices", tts_voices), ("voice", voice_name)):
        if not isinstance(value, str) or not value or Path(value).name != value:
            raise ValueError(f"voice.{key} must be a non-empty filename or identifier")
    if whisper not in {"tiny.en", "base.en", "small.en"}:
        raise ValueError("voice.whisper_model must be tiny.en, base.en, or small.en")
    return VoiceConfig(enabled, model_dir, whisper, tts_model, tts_voices, voice_name,
                       device, rate, maximum, float(silence))


def _validate_local_ollama_host(host: str, feature: str) -> None:
    message = f"{feature} requires a valid unauthenticated loopback Ollama host"
    if not isinstance(host, str) or not host or host.strip() != host:
        raise ValueError(message)
    try:
        parsed = urlsplit(host)
        hostname = parsed.hostname or ""
        port = parsed.port
    except ValueError as exc:
        raise ValueError(message) from exc
    try:
        is_loopback = ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        is_loopback = hostname.casefold() == "localhost"
    authority = parsed.netloc.rsplit("@", 1)[-1]
    invalid_url = (parsed.scheme != "http" or not parsed.netloc or not hostname
                   or parsed.path not in {"", "/"} or bool(parsed.query or parsed.fragment)
                   or "?" in host or "#" in host or authority.endswith(":")
                   or parsed.username is not None or parsed.password is not None
                   or (port is not None and not 1 <= port <= 65535))
    if not is_loopback or invalid_url:
        raise ValueError(message)


def _vision_config(raw) -> VisionConfig:
    defaults = VisionConfig()
    if not isinstance(raw, dict):
        raise ValueError("vision settings must be a TOML table")
    enabled = raw.get("enabled", defaults.enabled)
    model = raw.get("model", defaults.model)
    camera_index = raw.get("camera_index", defaults.camera_index)
    capture_timeout = raw.get("capture_timeout_seconds", defaults.capture_timeout_seconds)
    max_width = raw.get("max_width", defaults.max_width)
    max_height = raw.get("max_height", defaults.max_height)
    jpeg_quality = raw.get("jpeg_quality", defaults.jpeg_quality)
    max_image_bytes = raw.get("max_image_bytes", defaults.max_image_bytes)
    inference_timeout = raw.get("inference_timeout_seconds", defaults.inference_timeout_seconds)
    if not isinstance(enabled, bool):
        raise ValueError("vision.enabled must be a boolean")
    if not isinstance(model, str):
        raise ValueError("vision.model must be a string")
    if len(model) > 120 or any(char.isspace() for char in model.strip()):
        raise ValueError("vision.model must be a short Ollama model identifier")
    for name, value, minimum, maximum in (
        ("camera_index", camera_index, 0, 31),
        ("max_width", max_width, 160, 4096),
        ("max_height", max_height, 160, 4096),
        ("jpeg_quality", jpeg_quality, 40, 95),
        ("max_image_bytes", max_image_bytes, 16_384, 5_000_000),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"vision.{name} must be an integer from {minimum} to {maximum}")
    for name, value, maximum in (("capture_timeout_seconds", capture_timeout, 30),
                                 ("inference_timeout_seconds", inference_timeout, 300)):
        if (isinstance(value, bool) or not isinstance(value, (int, float))
                or not math.isfinite(value) or not 0.5 <= value <= maximum):
            raise ValueError(f"vision.{name} must be from 0.5 to {maximum} seconds")
    return VisionConfig(enabled, model.strip(), camera_index, float(capture_timeout),
                        max_width, max_height, jpeg_quality, max_image_bytes,
                        float(inference_timeout))


def _avatar_config(raw, project_root: Path) -> AvatarConfig:
    """Parse optional avatar settings without allowing them to fail app startup."""
    defaults = {
        "enabled": False,
        "asset_path": "assets/avatar/sylphy.png",
        "allow_external_asset": False,
        "width": 240,
        "height": 320,
        "always_on_top": False,
        "start_visible": True,
    }
    errors: list[str] = []
    if not isinstance(raw, dict):
        raw = {}
        errors.append("avatar settings must be a TOML table")
    values = dict(defaults)
    for key in ("enabled", "allow_external_asset", "always_on_top", "start_visible"):
        value = raw.get(key, defaults[key])
        if not isinstance(value, bool):
            errors.append(f"avatar.{key} must be a boolean")
            value = defaults[key]
        values[key] = value
    asset_path = raw.get("asset_path", defaults["asset_path"])
    if not isinstance(asset_path, str) or not asset_path.strip():
        errors.append("avatar.asset_path must be a non-empty path")
        asset_path = defaults["asset_path"]
    values["asset_path"] = Path(asset_path)
    for key in ("width", "height"):
        value = raw.get(key, defaults[key])
        if isinstance(value, bool) or not isinstance(value, int) or not 120 <= value <= 800:
            errors.append(f"avatar.{key} must be an integer from 120 to 800")
            value = defaults[key]
        values[key] = value
    return AvatarConfig(
        enabled=values["enabled"], asset_path=values["asset_path"],
        allow_external_asset=values["allow_external_asset"], width=values["width"],
        height=values["height"], always_on_top=values["always_on_top"],
        start_visible=values["start_visible"], project_root=project_root,
        config_error="; ".join(errors) or None,
    )
