"""Opt-in, local English speech input and output for the existing conversation path."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import logging
from queue import Empty, Full, Queue
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING, Callable

from .avatar import PresentationState
from .config import VoiceConfig
from .core import ConversationCancelled, ConversationService

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from .runtime import Runtime

FRAME_MS = 30
FRAME_SAMPLES = 16000 * FRAME_MS // 1000
MAX_NO_SPEECH_PROBABILITY = 0.6


@dataclass(frozen=True)
class VoiceDevice:
    index: int
    name: str
    input_channels: int
    default_sample_rate: float


class VoiceError(RuntimeError):
    """A recoverable local voice subsystem error."""


class VoiceService:
    """Runtime-owned one-microphone worker. Audio is held in memory only."""

    def __init__(self, config: VoiceConfig, runtime: "Runtime", conversation: ConversationService,
                 session_id: Callable[[], str | None], on_transcript: Callable[[str], None] | None = None,
                 on_response: Callable[[str], None] | None = None,
                 audio_module=None, vad_factory=None, stt_factory=None, tts_factory=None):
        self.config = config
        self._runtime = runtime
        self._conversation = conversation
        self._session_id = session_id
        self._on_transcript = on_transcript or (lambda text: None)
        self._on_response = on_response or (lambda text: None)
        self._audio_module = audio_module
        self._vad_factory = vad_factory
        self._stt_factory = stt_factory
        self._tts_factory = tts_factory
        self._lock = Lock()
        self._stop = Event()
        self._utterances: Queue[bytes] = Queue(maxsize=4)
        self._thread: Thread | None = None
        self._stream = None
        self._state = "disabled" if not config.enabled else "stopped"
        self._error: str | None = None
        self._interrupt = Event()
        self._generation_cancel: Event | None = None

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @staticmethod
    def devices(audio_module=None) -> list[VoiceDevice]:
        try:
            sd = audio_module or _sounddevice()
            return [VoiceDevice(index, str(device["name"]), int(device["max_input_channels"]),
                                float(device["default_samplerate"]))
                    for index, device in enumerate(sd.query_devices())
                    if int(device["max_input_channels"]) > 0]
        except Exception as exc:
            raise VoiceError(f"Could not enumerate microphone devices ({type(exc).__name__}).") from exc

    def start(self) -> None:
        if not self.config.enabled:
            raise VoiceError("Voice is disabled. Set [voice].enabled = true to opt in.")
        with self._lock:
            if self.running:
                return
            self._stop.clear()
            self._error = None
            self._state = "starting"
            self._thread = Thread(target=self._run, name="sylphy-voice", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 15.0) -> bool:
        self._stop.set()
        self.request_interrupt()
        stream = self._stream
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                pass
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        stopped = thread is None or not thread.is_alive()
        if stopped:
            with self._lock:
                self._state = "stopped" if self.config.enabled else "disabled"
        return stopped

    def request_interrupt(self) -> None:
        """Cancel active Ollama generation and/or invalidate current playback."""
        self._interrupt.set()
        cancel = self._generation_cancel
        if cancel is not None:
            cancel.set()

    def _set_state(self, state: str, avatar_state: PresentationState | None = None) -> None:
        with self._lock:
            self._state = state
        if avatar_state is not None:
            self._runtime.set_presentation_state(avatar_state)

    def _run(self) -> None:
        stream = None
        try:
            sd = self._audio_module or _sounddevice()
            vad = (self._vad_factory or _webrtc_vad)()
            whisper = (self._stt_factory or _load_whisper)(self.config)
            if self._stop.is_set():
                return
            tts = (self._tts_factory or _load_kokoro)(self.config)
            if self._stop.is_set():
                return
            self._set_state("listening", PresentationState.LISTENING)
            preroll: deque[bytes] = deque(maxlen=8)
            utterance: list[bytes] = []
            active = False
            silent_frames = 0
            max_frames = int(self.config.max_utterance_seconds * 1000 / FRAME_MS)
            silence_frames = max(1, int(self.config.silence_seconds * 1000 / FRAME_MS))

            def callback(indata, frames, timing, status) -> None:
                nonlocal active, silent_frames, utterance
                del timing
                if status:
                    logger.debug("Microphone callback status: %s", status)
                raw = indata[:, 0].tobytes()
                voiced = vad.is_speech(raw, self.config.sample_rate)
                if voiced:
                    if not active:
                        active = True
                        utterance = list(preroll)
                        preroll.clear()
                        silent_frames = 0
                        self.request_interrupt()
                    utterance.append(raw)
                    silent_frames = 0
                elif active:
                    utterance.append(raw)
                    silent_frames += 1
                else:
                    preroll.append(raw)
                if active and (silent_frames >= silence_frames or len(utterance) >= max_frames):
                    payload = b"".join(utterance)
                    try:
                        self._utterances.put_nowait(payload)
                    except Full:
                        logger.warning("Voice utterance queue is full; dropping newest utterance")
                    active = False
                    utterance = []
                    silent_frames = 0

            stream = sd.InputStream(
                device=self.config.microphone_device, channels=1,
                samplerate=self.config.sample_rate, dtype="int16",
                blocksize=FRAME_SAMPLES, callback=callback,
            )
            self._stream = stream
            stream.start()
            if self._stop.is_set():
                stream.stop()
                return
            print("[Voice] Microphone listening. Use /voice stop to stop immediately.", flush=True)
            while not self._stop.is_set():
                try:
                    payload = self._utterances.get(timeout=0.1)
                except Empty:
                    continue
                try:
                    if payload and not self._stop.is_set():
                        self._process_utterance(payload, sd, whisper, tts)
                finally:
                    # Do not keep microphone buffers alive while waiting for another utterance.
                    del payload
        except Exception as exc:
            if not self._stop.is_set():
                with self._lock:
                    self._error = f"{type(exc).__name__}: {exc}"
                logger.exception("Voice worker failed; text conversation remains available")
                print(f"[Voice] Unavailable: {type(exc).__name__}: {exc}", flush=True)
                self._set_state("unavailable", PresentationState.UNAVAILABLE)
        finally:
            self._stream = None
            if stream is not None:
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    logger.debug("Microphone cleanup failed", exc_info=True)
            if self.config.enabled and self._stop.is_set():
                self._set_state("stopped", PresentationState.IDLE)

    def _process_utterance(self, payload: bytes, sd, whisper, tts) -> None:
        self._interrupt.clear()
        if not payload or len(payload) % 2:
            logger.debug("Ignoring empty or malformed PCM utterance")
            self._set_state("listening", PresentationState.LISTENING)
            return
        audio = _pcm_to_float(payload)
        self._set_state("thinking", PresentationState.THINKING)
        try:
            segments, _info = whisper.transcribe(
                audio, language="en", beam_size=1,
                condition_on_previous_text=False, vad_filter=False,
            )
            text = " ".join(
                segment.text.strip() for segment in segments
                if float(getattr(segment, "no_speech_prob", 0.0)) <= MAX_NO_SPEECH_PROBABILITY
            ).strip()
        except Exception as exc:
            logger.warning("Local speech transcription failed (%s)", type(exc).__name__)
            self._set_state("listening", PresentationState.LISTENING)
            return
        if not text:
            self._set_state("listening", PresentationState.LISTENING)
            return
        self._on_transcript(text)
        session_id = self._session_id()
        if not session_id:
            self._set_state("listening", PresentationState.LISTENING)
            logger.warning("Voice transcript discarded because no conversation session is active")
            return
        cancel = Event()
        self._generation_cancel = cancel
        try:
            turn = self._runtime.run_conversation(
                self._conversation, session_id, text, cancel=cancel,
            )
        except (ConversationCancelled, InterruptedError):
            self._set_state("listening", PresentationState.LISTENING)
            return
        except Exception as exc:
            logger.warning("Voice conversation failed (%s)", type(exc).__name__)
            self._set_state("listening", PresentationState.LISTENING)
            return
        finally:
            self._generation_cancel = None
        if self._stop.is_set() or cancel.is_set():
            self._set_state("listening", PresentationState.LISTENING)
            return
        answer = turn.assistant.content
        self._on_response(answer)
        try:
            audio_out, sample_rate = tts.create(answer, voice=self.config.voice,
                                                speed=1.0, lang="en-us")
        except Exception as exc:
            logger.warning("Local speech synthesis failed (%s)", type(exc).__name__)
            self._set_state("listening", PresentationState.LISTENING)
            return
        if self._stop.is_set() or self._interrupt.is_set():
            self._set_state("listening", PresentationState.LISTENING)
            return
        try:
            self._play(sd, audio_out, sample_rate)
        except Exception as exc:
            logger.warning("Audio playback failed (%s)", type(exc).__name__)
        self._interrupt.clear()
        self._set_state("listening", PresentationState.LISTENING)

    def _play(self, sd, samples, sample_rate: int) -> None:
        import numpy as np

        data = np.asarray(samples, dtype=np.float32).reshape(-1, 1)
        block_size = max(1, int(sample_rate * 0.02))
        self._set_state("speaking", PresentationState.SPEAKING)
        with sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32", blocksize=block_size) as output:
            for offset in range(0, len(data), block_size):
                if self._stop.is_set() or self._interrupt.is_set():
                    try:
                        output.abort()
                    except Exception:
                        pass
                    self._set_state("listening", PresentationState.LISTENING)
                    return
                output.write(data[offset:offset + block_size])


def _sounddevice():
    try:
        import sounddevice
        return sounddevice
    except ImportError as exc:
        raise VoiceError("Install SYLPHY's optional voice dependencies to use voice.") from exc


def _webrtc_vad():
    try:
        import webrtcvad
        return webrtcvad.Vad(2)
    except ImportError as exc:
        raise VoiceError("The local WebRTC VAD component is unavailable.") from exc


def _load_whisper(config: VoiceConfig):
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise VoiceError("Install SYLPHY's optional voice dependencies to use voice.") from exc
    config.model_dir.mkdir(parents=True, exist_ok=True)
    return WhisperModel(config.whisper_model, device="cpu", compute_type="int8",
                        cpu_threads=4, download_root=str(config.model_dir / "whisper"))


def _load_kokoro(config: VoiceConfig):
    try:
        from kokoro_onnx import Kokoro
    except ImportError as exc:
        raise VoiceError("Install SYLPHY's optional voice dependencies to use voice.") from exc
    model = config.model_dir / config.tts_model
    voices = config.model_dir / config.tts_voices
    if not model.is_file() or not voices.is_file():
        raise VoiceError(f"Kokoro assets are missing from {config.model_dir}; see README voice setup.")
    return Kokoro(str(model), str(voices))


def _pcm_to_float(payload: bytes):
    import numpy as np
    return np.frombuffer(payload, dtype=np.int16).astype(np.float32) / 32768.0
