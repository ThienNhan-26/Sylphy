"""TTS and audio-device ports plus lazy local adapters."""
from __future__ import annotations
import asyncio
from pathlib import Path
from typing import Protocol
import numpy as np
from .contracts import VoiceAudio, VoiceSession


class TTSProvider(Protocol):
    async def synthesize(self, session: VoiceSession) -> VoiceAudio: ...


class AudioOutput(Protocol):
    async def play(self, audio: VoiceAudio, *, on_started=None, progress=None) -> None: ...
    async def stop(self) -> None: ...


class KokoroOnnxProvider:
    """Offline Kokoro ONNX provider. Model/voice assets are supplied by the user."""
    def __init__(self, model_path: str | Path, voices_path: str | Path):
        self.model_path = Path(model_path)
        self.voices_path = Path(voices_path)
        self._engine = None

    def _load(self):
        if not self.model_path.is_file() or not self.voices_path.is_file():
            raise FileNotFoundError("Kokoro ONNX model and voices files must both exist")
        if self._engine is None:
            try:
                from kokoro_onnx import Kokoro
            except ImportError as exc:
                raise RuntimeError("Install the optional 'voice-output' dependencies first") from exc
            self._engine = Kokoro(str(self.model_path), str(self.voices_path))
        return self._engine

    async def synthesize(self, session: VoiceSession) -> VoiceAudio:
        def run():
            samples, rate = self._load().create(
                session.text, voice=session.voice, speed=session.speed, lang=session.language
            )
            array = np.asarray(samples, dtype=np.float32)
            return VoiceAudio(array, int(rate), len(array) / int(rate))
        return await asyncio.to_thread(run)


class SoundDeviceOutput:
    """Callback-driven speaker output with a playback-position clock.

    Position is derived from frames handed to the host audio stream, not wall-clock
    time. The stream callback runs on the audio thread; progress notifications are
    marshalled back onto the asyncio event loop.
    """

    def __init__(self, *, progress_interval_seconds: float = 0.05, device=None):
        if progress_interval_seconds <= 0:
            raise ValueError("progress_interval_seconds must be positive")
        self.progress_interval_seconds = progress_interval_seconds
        self.device = device
        self._stream = None
        self._stream_lock = __import__("threading").Lock()
        self._stop_requested = False

    async def play(self, audio: VoiceAudio, *, on_started=None, progress=None) -> None:
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("Install the optional 'voice-output' dependencies first") from exc

        samples = np.asarray(audio.samples, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            raise ValueError("audio samples must not be empty")
        if not np.isfinite(samples).all():
            raise ValueError("audio samples must contain only finite values")

        loop = asyncio.get_running_loop()
        finished = asyncio.Event()
        callbacks: asyncio.Queue[tuple[str, float, float, float] | None] = asyncio.Queue()
        total_frames = int(samples.size)
        progress_step = max(1, int(audio.sample_rate * self.progress_interval_seconds))
        cursor = 0
        last_reported = 0
        started_notified = False
        self._stop_requested = False

        async def dispatch_callbacks() -> None:
            while True:
                item = await callbacks.get()
                if item is None:
                    return
                kind, position, duration, amplitude = item
                if kind == "started":
                    if on_started is not None:
                        result = on_started()
                        if asyncio.iscoroutine(result):
                            await result
                    continue
                if progress is not None:
                    result = progress(position, duration, amplitude)
                    if asyncio.iscoroutine(result):
                        await result

        def notify(kind: str, position: float = 0.0, amplitude: float = 0.0) -> None:
            loop.call_soon_threadsafe(
                callbacks.put_nowait,
                (kind, position, audio.duration_seconds, amplitude),
            )

        def on_finished() -> None:
            loop.call_soon_threadsafe(finished.set)
            loop.call_soon_threadsafe(callbacks.put_nowait, None)

        def callback(outdata, frames, time_info, status):
            nonlocal cursor, last_reported, started_notified
            outdata.fill(0)
            if self._stop_requested:
                raise sd.CallbackStop

            start = cursor
            end = min(cursor + frames, total_frames)
            count = end - start
            if count > 0:
                outdata[:count, 0] = samples[start:end]
                cursor = end
                # Playback is considered started only after real frames have been
                # handed to the host output callback, not after TTS synthesis.
                if not started_notified:
                    started_notified = True
                    notify("started")

                if cursor - last_reported >= progress_step or cursor >= total_frames:
                    analysis_start = last_reported
                    block = samples[analysis_start:cursor]
                    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64)))) if block.size else 0.0
                    amplitude = max(0.0, min(rms, 1.0))
                    last_reported = cursor
                    notify(
                        "progress",
                        min(cursor / audio.sample_rate, audio.duration_seconds),
                        amplitude,
                    )

            if cursor >= total_frames:
                raise sd.CallbackStop

        stream = sd.OutputStream(
            samplerate=audio.sample_rate,
            channels=1,
            dtype="float32",
            device=self.device,
            callback=callback,
            finished_callback=on_finished,
        )
        with self._stream_lock:
            self._stream = stream
        dispatcher = asyncio.create_task(dispatch_callbacks(), name="voice-audio-progress")
        try:
            stream.start()
            await finished.wait()
            await dispatcher
            if self._stop_requested:
                raise asyncio.CancelledError
        finally:
            if not dispatcher.done():
                dispatcher.cancel()
                await asyncio.gather(dispatcher, return_exceptions=True)
            with self._stream_lock:
                if self._stream is stream:
                    self._stream = None
            try:
                stream.stop()
            finally:
                stream.close()

    async def stop(self) -> None:
        with self._stream_lock:
            stream = self._stream
            self._stop_requested = True
        if stream is not None:
            await asyncio.to_thread(stream.abort)
