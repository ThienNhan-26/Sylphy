from pathlib import Path

from PIL import Image
from PIL import ImageDraw, ImageFont, ImageOps

from sylphy.avatar import (
    AvatarConfig, AvatarController, PresentationState, SurfaceStatus,
    WindowsAvatarSurface,
)
from sylphy.config import load_config
from sylphy.context import ContextBuilder
from sylphy.core import ConversationService
from sylphy.runtime import Runtime, RuntimePhase
from sylphy.storage import SQLiteStore


class FakeSurface:
    def __init__(self, *, fail_state=False):
        self.starts = 0
        self.stops = 0
        self.visible = False
        self.running = False
        self.available = False
        self.always_on_top = False
        self.states = []
        self.fail_state = fail_state
        self.error = None

    def start(self, config):
        self.starts += 1
        self.running = True
        self.available = True
        self.visible = config.start_visible
        self.always_on_top = config.always_on_top

    def show(self):
        self.visible = True
        return self.running

    def hide(self):
        self.visible = False
        return self.running

    def set_state(self, state):
        if self.fail_state:
            raise OSError("fake surface failure")
        self.states.append(state)
        return self.running

    def set_always_on_top(self, enabled):
        self.always_on_top = enabled
        return self.running

    def stop(self):
        self.stops += 1
        self.running = False
        self.available = False
        self.visible = False

    def status(self):
        return SurfaceStatus(self.running, self.available, self.visible,
                             self.always_on_top, self.error)


class FakeProvider:
    def __init__(self, response="local reply"):
        self.response = response
        self.calls = 0

    def reply(self, messages, cancel=None, on_chunk=None):
        self.calls += 1
        if on_chunk:
            on_chunk(self.response)
        return self.response


def avatar_config(**kwargs):
    return AvatarConfig(enabled=True, project_root=Path.cwd(), **kwargs)


def test_disabled_configuration_does_not_start_surface_and_repeated_calls_are_safe():
    surface = FakeSurface()
    controller = AvatarController(AvatarConfig(enabled=False), surface)
    controller.start()
    controller.start()
    assert surface.starts == 0
    assert not controller.show()
    assert not controller.hide()
    controller.stop()
    controller.stop()
    assert surface.stops == 2


def test_enabled_controller_starts_one_surface_and_supports_controls_and_states():
    surface = FakeSurface()
    controller = AvatarController(avatar_config(), surface)
    controller.start()
    controller.start()
    assert surface.starts == 1
    assert controller.show() and controller.show()
    assert controller.hide() and controller.hide()
    assert controller.set_always_on_top(True)
    for state in (PresentationState.IDLE, PresentationState.THINKING,
                  PresentationState.SPEAKING, PresentationState.CURIOUS,
                  PresentationState.OBSERVING):
        assert controller.set_state(state)
    assert not controller.set_state("NOT_A_STATE")
    assert surface.states == [PresentationState.IDLE, PresentationState.IDLE, PresentationState.THINKING,
                              PresentationState.SPEAKING, PresentationState.CURIOUS,
                              PresentationState.OBSERVING]
    assert controller.status().always_on_top
    controller.stop()
    controller.stop()
    assert surface.stops == 2
    assert controller.status().state is PresentationState.UNAVAILABLE


def test_missing_asset_renders_identified_placeholder_and_reports_status(tmp_path):
    surface = WindowsAvatarSurface()
    config = AvatarConfig(enabled=True, asset_path=Path("assets/avatar/missing.png"),
                          project_root=tmp_path, width=240, height=320)
    surface._config = config
    pixels, diagnostic = surface._render_asset(Image, ImageDraw, ImageFont, ImageOps)
    assert len(pixels) == 240 * 320 * 4
    assert "development placeholder" in diagnostic.casefold()
    assert WindowsAvatarSurface._resolve_asset(config) is None

    surface_adapter = FakeSurface()
    surface_adapter.error = "Avatar asset is missing; using development placeholder."
    controller = AvatarController(config, surface_adapter)
    controller.start()
    status = controller.status()
    assert status.available and "development placeholder" in status.error
    controller.stop()

    invalid = tmp_path / "assets" / "avatar" / "invalid.png"
    invalid.parent.mkdir(parents=True, exist_ok=True)
    invalid.write_text("not a png", encoding="utf-8")
    surface._config = AvatarConfig(enabled=True, asset_path=Path("assets/avatar/invalid.png"),
                                   project_root=tmp_path)
    _, invalid_diagnostic = surface._render_asset(Image, ImageDraw, ImageFont, ImageOps)
    assert "could not be loaded" in invalid_diagnostic


def test_external_asset_requires_explicit_configuration_and_fit_preserves_aspect(tmp_path):
    asset_dir = tmp_path / "assets" / "avatar"
    asset_dir.mkdir(parents=True)
    asset = asset_dir / "local.png"
    Image.new("RGBA", (400, 200), (220, 30, 40, 255)).save(asset)
    config = AvatarConfig(enabled=True, asset_path=Path("assets/avatar/local.png"),
                          project_root=tmp_path, width=240, height=320)
    assert WindowsAvatarSurface._resolve_asset(config) == asset.resolve()
    outside = tmp_path / "owner-art.png"
    Image.new("RGBA", (400, 200), (220, 30, 40, 255)).save(outside)
    assert WindowsAvatarSurface._resolve_asset(
        AvatarConfig(enabled=True, asset_path=outside, project_root=tmp_path)
    ) is None
    assert WindowsAvatarSurface._resolve_asset(
        AvatarConfig(enabled=True, asset_path=outside, project_root=tmp_path, allow_external_asset=True)
    ) == outside.resolve()

    surface = WindowsAvatarSurface()
    surface._config = config
    pixels, diagnostic = surface._render_asset(Image, ImageDraw, ImageFont, ImageOps)
    assert diagnostic is None
    rendered = Image.frombytes("RGB", (240, 320), pixels, "raw", "BGRX")
    red = rendered.convert("RGB")
    mask = red.point(lambda value: 255 if value > 150 else 0).getbbox()
    # The drawn image occupies 240 x 120 pixels (2:1), centered in the canvas.
    assert mask == (12, 106, 228, 214)
    assert (mask[2] - mask[0]) / (mask[3] - mask[1]) == 2


def test_avatar_config_is_conservative_and_invalid_settings_do_not_abort_config_load(tmp_path):
    config_file = tmp_path / "sylphy.toml"
    config_file.write_text('[ollama]\nmodel="test"\n[avatar]\nenabled=true\nwidth=20\n', encoding="utf-8")
    config = load_config(config_file)
    assert config.avatar.enabled
    assert config.avatar.width == 240
    assert config.avatar.config_error and "avatar.width" in config.avatar.config_error
    assert config.avatar.project_root == tmp_path
    defaults_path = tmp_path / "defaults.toml"
    defaults_path.write_text("", encoding="utf-8")
    disabled = load_config(defaults_path)
    assert disabled.avatar.enabled is False
    assert disabled.avatar.always_on_top is False


def test_runtime_forwards_states_and_avatar_failure_does_not_fail_conversation(tmp_path):
    store = SQLiteStore(tmp_path / "avatar-runtime.db")
    session = store.create_session()
    provider = FakeProvider()
    service = ConversationService(provider, store, ContextBuilder("sys", "id", 1000))
    surface = FakeSurface()
    controller = AvatarController(avatar_config(), surface)
    runtime = Runtime("fake", heartbeat_enabled=False)
    runtime.configure_avatar(controller)
    runtime.start()
    runtime.set_presentation_state(PresentationState.CURIOUS)
    assert surface.states[-1] is PresentationState.CURIOUS
    assert provider.calls == 0
    runtime.change_session(session.id)
    try:
        turn = runtime.run_conversation(service, session.id, "hello", on_chunk=lambda _chunk: None)
        assert turn.assistant.content == "local reply"
        assert provider.calls == 1
        assert surface.states == [PresentationState.IDLE, PresentationState.CURIOUS,
                                  PresentationState.IDLE,
                                  PresentationState.THINKING, PresentationState.SPEAKING,
                                  PresentationState.IDLE]
        assert runtime.state.snapshot().phase is RuntimePhase.IDLE
    finally:
        runtime.shutdown()
    assert surface.stops == 1
    assert not surface.running

    broken = FakeSurface(fail_state=True)
    controller2 = AvatarController(avatar_config(), broken)
    runtime2 = Runtime("fake", heartbeat_enabled=False)
    runtime2.configure_avatar(controller2)
    runtime2.start()
    session2 = store.create_session()
    try:
        turn = runtime2.run_conversation(service, session2.id, "still works")
        assert turn.assistant.content == "local reply"
        assert runtime2.state.snapshot().phase is RuntimePhase.IDLE
        assert controller2.status().state is PresentationState.UNAVAILABLE
    finally:
        runtime2.shutdown()


def test_window_initialization_failure_does_not_fail_runtime_or_conversation(tmp_path):
    class BrokenSurface(FakeSurface):
        def start(self, _config):
            raise OSError("native window unavailable")

    store = SQLiteStore(tmp_path / "avatar-unavailable.db")
    session = store.create_session()
    controller = AvatarController(avatar_config(), BrokenSurface())
    runtime = Runtime("fake", heartbeat_enabled=False)
    runtime.configure_avatar(controller)
    runtime.start()
    try:
        assert runtime.state.snapshot().phase is RuntimePhase.READY
        assert controller.status().state is PresentationState.UNAVAILABLE
        service = ConversationService(FakeProvider(), store, ContextBuilder("sys", "id", 1000))
        result = runtime.run_conversation(service, session.id, "continue without window")
        assert result.assistant.content == "local reply"
        assert runtime.state.snapshot().phase is RuntimePhase.IDLE
    finally:
        runtime.shutdown()


def test_avatar_commands_do_not_take_focus_or_change_console_input(monkeypatch, capsys):
    from sylphy.app import _avatar_command

    surface = FakeSurface()
    controller = AvatarController(avatar_config(), surface)
    runtime = Runtime("fake", heartbeat_enabled=False)
    runtime.configure_avatar(controller)
    runtime.start()
    prompts = []
    monkeypatch.setattr("builtins.input", lambda prompt: prompts.append(prompt) or "hello")
    try:
        _avatar_command("/avatar hide", runtime)
        assert input("You: ") == "hello"
        assert prompts == ["You: "]
        assert "hidden" in capsys.readouterr().out
    finally:
        runtime.shutdown()


def test_native_surface_can_be_stopped_before_start():
    surface = WindowsAvatarSurface()
    surface.stop()
    surface.stop()
    assert not surface.show()
    assert not surface.hide()
