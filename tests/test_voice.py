from pathlib import Path
from threading import Event
from types import SimpleNamespace
import time

import numpy as np
import pytest

from sylphy.avatar import PresentationState
from sylphy.config import VoiceConfig, load_config
from sylphy.voice import VoiceError, VoiceService, _pcm_to_float


def test_voice_configuration_is_opt_in_and_validated(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[sylphy]\ndata_dir='state'\n", encoding="utf-8")
    config = load_config(path)
    assert config.voice.enabled is False
    assert config.voice.whisper_model == "base.en"
    assert config.voice.voice == "af_heart"
    assert config.voice.model_dir == tmp_path / "state" / "voice-models"

    path.write_text("[voice]\nenabled=true\nwhisper_model='small.en'\nmicrophone_device=1\n", encoding="utf-8")
    config = load_config(path)
    assert config.voice.enabled and config.voice.whisper_model == "small.en"
    assert config.voice.microphone_device == 1
    path.write_text("[voice]\nenabled='yes'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="voice.enabled"):
        load_config(path)
    path.write_text("[voice]\nwhisper_model='large'\n", encoding="utf-8")
    with pytest.raises(ValueError, match="whisper_model"):
        load_config(path)
    path.write_text("[voice]\nmodel_dir=17\n", encoding="utf-8")
    with pytest.raises(ValueError, match="model_dir"):
        load_config(path)


def test_disabled_voice_does_not_start_or_open_microphone():
    service = VoiceService(VoiceConfig(enabled=False), None, None, lambda: None)
    with pytest.raises(VoiceError, match="disabled"):
        service.start()
    assert service.state == "disabled"


def test_voice_cli_status_and_disabled_start(capfd):
    from sylphy.app import _voice_command

    service = VoiceService(VoiceConfig(enabled=False), None, None, lambda: None)
    _voice_command("/voice status", service)
    assert "Voice: disabled" in capfd.readouterr().out
    _voice_command("/voice start", service)
    assert "disabled" in capfd.readouterr().out


def test_empty_pcm_is_empty_and_valid_pcm_converts():
    assert _pcm_to_float(b"").size == 0
    values = _pcm_to_float(np.array([0, 16384, -16384], dtype=np.int16).tobytes())
    assert values.tolist() == [0.0, 0.5, -0.5]


@pytest.mark.parametrize("payload", [b"", b"\x01"])
def test_empty_or_invalid_audio_is_ignored(payload):
    runtime = _FakeRuntime()
    service = VoiceService(VoiceConfig(enabled=True), runtime, object(), lambda: "s")
    service._process_utterance(payload, _FakeAudio(), _FakeWhisper(), _FakeTts())
    assert runtime.calls == []
    assert service.state == "listening"


def test_device_enumeration_filters_non_input_devices():
    class Devices:
        @staticmethod
        def query_devices():
            return [
                {"name": "Mic", "max_input_channels": 2, "default_samplerate": 48000},
                {"name": "Speaker", "max_input_channels": 0, "default_samplerate": 48000},
            ]

    devices = VoiceService.devices(Devices)
    assert [(d.index, d.name) for d in devices] == [(0, "Mic")]


def test_device_enumeration_reports_unavailable_audio():
    class MissingAudio:
        @staticmethod
        def query_devices():
            raise OSError("no PortAudio device")

    with pytest.raises(VoiceError, match="enumerate"):
        VoiceService.devices(MissingAudio)


def test_microphone_open_failure_marks_voice_unavailable():
    class BadAudio:
        @staticmethod
        def InputStream(**kwargs):
            raise OSError("microphone busy")

    service = VoiceService(
        VoiceConfig(enabled=True), _FakeRuntime(), object(), lambda: "s",
        audio_module=BadAudio, vad_factory=_FakeVad,
        stt_factory=lambda config: _FakeWhisper(), tts_factory=lambda config: _FakeTts(),
    )
    service.start()
    assert _eventually(lambda: service.state == "unavailable")
    assert "microphone busy" in service.error
    assert not service.running


class _FakeStream:
    def __init__(self, callback, **kwargs):
        self.callback = callback
        self.closed = False
        self.aborted = False

    def start(self):
        for _ in range(9):
            self.callback(np.zeros((480, 1), dtype=np.int16), 480, None, None)
        for _ in range(3):
            self.callback(np.full((480, 1), 1000, dtype=np.int16), 480, None, None)
        for _ in range(28):
            self.callback(np.zeros((480, 1), dtype=np.int16), 480, None, None)

    def stop(self):
        pass

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def write(self, data):
        pass

    def abort(self):
        self.aborted = True


class _FakeAudio:
    def __init__(self):
        self.input_stream = None
        self.output_stream = None

    def InputStream(self, **kwargs):
        self.input_stream = _FakeStream(**kwargs)
        return self.input_stream

    def OutputStream(self, **kwargs):
        self.output_stream = _FakeStream(lambda *args: None, **kwargs)
        return self.output_stream


class _FakeVad:
    @staticmethod
    def is_speech(frame, rate):
        del rate
        return int.from_bytes(frame[:2], "little", signed=True) > 100


class _FakeWhisper:
    def __init__(self, text="Hello Sylphy, can you hear me?", failure=None):
        self.text, self.failure = text, failure
        self.calls = 0

    def transcribe(self, audio, **kwargs):
        assert kwargs["language"] == "en"
        assert audio.size > 0
        self.calls += 1
        if self.failure:
            raise self.failure
        return [SimpleNamespace(text=self.text)], SimpleNamespace(language="en")


class _FakeTts:
    def __init__(self, failure=None):
        self.failure = failure
        self.calls = []

    def create(self, text, **kwargs):
        self.calls.append((text, kwargs))
        if self.failure:
            raise self.failure
        return np.zeros(200, dtype=np.float32), 1000


class _FakeRuntime:
    def __init__(self):
        self.states = []
        self.calls = []

    def set_presentation_state(self, state):
        self.states.append(state)

    def run_conversation(self, service, session_id, text, cancel=None, **kwargs):
        self.calls.append((session_id, text))
        return SimpleNamespace(assistant=SimpleNamespace(content="Yes, I can hear you."))


def _eventually(predicate, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_voice_transcription_conversation_tts_and_states():
    audio = _FakeAudio()
    runtime = _FakeRuntime()
    whisper = _FakeWhisper()
    tts = _FakeTts()
    transcripts, responses = [], []
    service = VoiceService(
        VoiceConfig(enabled=True), runtime, object(), lambda: "session-1",
        transcripts.append, responses.append, audio, _FakeVad,
        lambda config: whisper, lambda config: tts,
    )
    service.start()
    assert _eventually(lambda: bool(responses))
    assert transcripts == ["Hello Sylphy, can you hear me?"]
    assert runtime.calls == [("session-1", transcripts[0])]
    assert responses == ["Yes, I can hear you."]
    assert tts.calls[0][1]["voice"] == "af_heart"
    assert PresentationState.LISTENING in runtime.states
    assert PresentationState.THINKING in runtime.states
    assert PresentationState.SPEAKING in runtime.states
    assert service.stop()
    assert audio.input_stream.closed


def test_empty_transcription_does_not_call_conversation():
    audio = _FakeAudio()
    runtime = _FakeRuntime()
    whisper = _FakeWhisper(text="  ")
    service = VoiceService(VoiceConfig(enabled=True), runtime, object(), lambda: "s",
                           audio_module=audio, vad_factory=_FakeVad,
                           stt_factory=lambda config: whisper, tts_factory=lambda config: _FakeTts())
    service.start()
    assert _eventually(lambda: whisper.calls == 1)
    assert runtime.calls == []
    assert service.stop()


def test_high_no_speech_probability_segment_is_discarded():
    audio = _FakeAudio()
    runtime = _FakeRuntime()

    class SilenceWhisper:
        calls = 0

        def transcribe(self, audio, **kwargs):
            self.calls += 1
            return [SimpleNamespace(text=" You", no_speech_prob=0.77)], SimpleNamespace(language="en")

    whisper = SilenceWhisper()
    service = VoiceService(VoiceConfig(enabled=True), runtime, object(), lambda: "s",
                           audio_module=audio, vad_factory=_FakeVad,
                           stt_factory=lambda config: whisper, tts_factory=lambda config: _FakeTts())
    service.start()
    assert _eventually(lambda: whisper.calls == 1)
    assert runtime.calls == []
    assert service.stop()


def test_transcription_failure_recovers_without_runtime_crash():
    audio = _FakeAudio()
    runtime = _FakeRuntime()
    whisper = _FakeWhisper(failure=RuntimeError("local model failure"))
    service = VoiceService(VoiceConfig(enabled=True), runtime, object(), lambda: "s",
                           audio_module=audio, vad_factory=_FakeVad,
                           stt_factory=lambda config: whisper, tts_factory=lambda config: _FakeTts())
    service.start()
    assert _eventually(lambda: whisper.calls == 1)
    assert service.state == "listening"
    assert service.stop()


def test_tts_failure_keeps_voice_worker_available():
    audio = _FakeAudio()
    runtime = _FakeRuntime()
    whisper = _FakeWhisper()
    tts = _FakeTts(failure=RuntimeError("TTS failed"))
    service = VoiceService(VoiceConfig(enabled=True), runtime, object(), lambda: "s",
                           audio_module=audio, vad_factory=_FakeVad,
                           stt_factory=lambda config: whisper, tts_factory=lambda config: tts)
    service.start()
    assert _eventually(lambda: len(runtime.calls) == 1)
    assert _eventually(lambda: service.state == "listening")
    assert service.state == "listening"
    assert service.error is None
    assert service.stop()


def test_playback_failure_is_reported_to_caller_without_voice_crash():
    class BrokenAudio:
        @staticmethod
        def OutputStream(**kwargs):
            raise OSError("speaker unavailable")

    runtime = _FakeRuntime()
    service = VoiceService(VoiceConfig(enabled=True), runtime, object(), lambda: "s")
    service._process_utterance(
        np.ones(480, dtype=np.int16).tobytes(), BrokenAudio,
        _FakeWhisper(), _FakeTts(),
    )
    assert runtime.calls == [("s", "Hello Sylphy, can you hear me?")]
    assert service.state == "listening"
    assert service.error is None


def test_interrupt_stops_playback_before_next_chunk():
    audio = _FakeAudio()
    runtime = _FakeRuntime()
    service = VoiceService(VoiceConfig(enabled=True), runtime, object(), lambda: None)
    writes = []

    class InterruptOutput(_FakeStream):
        def write(self, data):
            writes.append(data)
            service._interrupt.set()

    class InterruptAudio:
        @staticmethod
        def OutputStream(**kwargs):
            return InterruptOutput(lambda *args: None, **kwargs)

    service._play(InterruptAudio, np.zeros(2000, dtype=np.float32), 1000)
    assert len(writes) == 1
    assert runtime.states == [PresentationState.SPEAKING, PresentationState.LISTENING]


def test_interrupt_propagates_to_active_conversation_cancellation():
    service = VoiceService(VoiceConfig(enabled=True), _FakeRuntime(), object(), lambda: None)
    cancel = Event()
    service._generation_cancel = cancel
    service.request_interrupt()
    assert service._interrupt.is_set()
    assert cancel.is_set()


def test_runtime_shutdown_stops_owned_voice_worker():
    from sylphy.runtime import Runtime, RuntimePhase

    class StoppableVoice:
        def __init__(self):
            self.stopped = False

        def stop(self):
            self.stopped = True
            return True

    runtime = Runtime("test", heartbeat_enabled=False, presence_enabled=False)
    voice = StoppableVoice()
    runtime.configure_voice(voice)
    runtime.start()
    runtime.shutdown()
    assert voice.stopped
    assert runtime.state.snapshot().phase == RuntimePhase.STOPPED
