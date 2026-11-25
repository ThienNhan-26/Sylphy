"""Consent-gated microphone adapter with ephemeral audio processing.

Audio samples remain in process memory. Processors must return bounded JSON
metadata/transcripts; raw audio bytes or arrays are never persisted.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from sylphy_entity.contracts.observation import Observation
from sylphy_entity.contracts.sensor import SensorKind
from .session import SensorAdapterSession


@dataclass(frozen=True, slots=True)
class CapturedAudio:
    samples: Any
    captured_at: datetime
    sequence: int
    sample_rate: int
    channels: int
    frames: int

    def __post_init__(self) -> None:
        if self.captured_at.tzinfo is None:
            raise ValueError("captured_at must be timezone-aware")
        if self.sequence < 0:
            raise ValueError("audio sequence must be non-negative")
        if self.sample_rate <= 0 or self.channels <= 0 or self.frames <= 0:
            raise ValueError("audio format and frame count must be positive")


class MicrophoneBackend(Protocol):
    async def open(self) -> None: ...
    async def read(self) -> CapturedAudio | None: ...
    async def close(self) -> None: ...


class AudioProcessor(Protocol):
    async def process(self, audio: CapturedAudio) -> Mapping[str, Any]: ...


class MicrophoneAdapter:
    """Captures audio only while explicit consent and device lease are active."""

    def __init__(
        self,
        *,
        device_id: str,
        session: SensorAdapterSession,
        backend: MicrophoneBackend,
        processor: AudioProcessor,
        max_audio_seconds: float = 10.0,
        max_payload_chars: int = 8192,
    ) -> None:
        if session.sensor is not SensorKind.MICROPHONE:
            raise ValueError("MicrophoneAdapter requires a MICROPHONE sensor session")
        if session.device_id != device_id:
            raise ValueError("microphone device_id must match sensor session")
        if not math.isfinite(max_audio_seconds) or max_audio_seconds <= 0:
            raise ValueError("max_audio_seconds must be finite and positive")
        if max_payload_chars <= 0:
            raise ValueError("max_payload_chars must be positive")
        self.device_id = device_id
        self._session = session
        self._backend = backend
        self._processor = processor
        self._max_audio_seconds = max_audio_seconds
        self._max_payload_chars = max_payload_chars
        self._task: asyncio.Task[None] | None = None
        self._opened = False
        self.last_error: str | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            return
        await self._session.start()
        try:
            await self._backend.open()
            self._opened = True
            self.last_error = None
            self._task = asyncio.create_task(
                self._capture_loop(),
                name=f"microphone-adapter:{self.device_id}",
            )
        except BaseException:
            await self._close_backend()
            await self._session.stop()
            raise

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await self._close_backend()
        await self._session.stop()

    async def _capture_loop(self) -> None:
        try:
            while True:
                audio = await self._backend.read()
                if audio is None:
                    await asyncio.sleep(0)
                    continue
                duration = audio.frames / audio.sample_rate
                if duration > self._max_audio_seconds:
                    raise ValueError("microphone chunk exceeds configured duration limit")
                result = await self._processor.process(audio)
                if not isinstance(result, Mapping):
                    raise TypeError("audio processor must return a mapping")
                if _contains_raw_audio(result):
                    raise ValueError("raw audio media is not accepted in microphone observations")
                import json
                encoded = json.dumps(dict(result), ensure_ascii=False, separators=(",", ":"))
                if len(encoded) > self._max_payload_chars:
                    raise ValueError("hearing result exceeds configured payload limit")
                observation = Observation(
                    source_id=f"sensor:microphone:{self.device_id}",
                    subject=f"microphone:{self.device_id}",
                    kind="microphone.audio.processed",
                    payload=dict(result),
                    observed_at=audio.captured_at,
                    received_at=datetime.now(timezone.utc),
                    sequence=audio.sequence,
                    verification="inferred",
                    permission_scope=self._session.active_permission_scope or "",
                    freshness="current",
                )
                await self._session.publish(observation)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
        finally:
            # Keep the adapter visibly RUNNING until cleanup has released the
            # device and its ResourceArbiter lease. Otherwise observers can see
            # running=False and race a still-pending session.stop().
            try:
                await self._close_backend()
            finally:
                try:
                    await self._session.stop()
                finally:
                    if asyncio.current_task() is self._task:
                        self._task = None

    async def _close_backend(self) -> None:
        if self._opened:
            self._opened = False
            await self._backend.close()


class AudioMetadataProcessor:
    """Emits bounded audio metadata and RMS level; performs no transcription."""

    async def process(self, audio: CapturedAudio) -> Mapping[str, Any]:
        try:
            import numpy as np
            samples = np.asarray(audio.samples, dtype=np.float64)
            if samples.size == 0:
                raise ValueError("audio chunk is empty")
            rms = float(np.sqrt(np.mean(np.square(samples))))
        except ImportError as exc:
            raise RuntimeError("numpy is required for AudioMetadataProcessor") from exc
        if not math.isfinite(rms):
            raise ValueError("audio RMS is not finite")
        return {
            "sample_rate": audio.sample_rate,
            "channels": audio.channels,
            "frames": audio.frames,
            "duration_seconds": round(audio.frames / audio.sample_rate, 6),
            "rms_level": round(rms, 8),
            "analysis": "audio_metadata_only",
        }


class SoundDeviceMicrophoneBackend:
    """Optional sounddevice backend; opens hardware only when explicitly started."""

    def __init__(
        self,
        device: int | str | None = None,
        *,
        sample_rate: int = 16000,
        channels: int = 1,
        chunk_seconds: float = 1.0,
    ) -> None:
        if sample_rate <= 0 or channels <= 0 or not math.isfinite(chunk_seconds) or chunk_seconds <= 0:
            raise ValueError("invalid microphone format")
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_seconds = chunk_seconds
        self._sounddevice: Any | None = None
        self._sequence = 0
        self._opened = False

    async def open(self) -> None:
        if self._opened:
            return
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise RuntimeError("install the optional voice extra to use microphone capture") from exc
        self._sounddevice = sd
        await asyncio.to_thread(
            sd.check_input_settings,
            device=self.device,
            channels=self.channels,
            samplerate=self.sample_rate,
        )
        self._opened = True

    async def read(self) -> CapturedAudio | None:
        if not self._opened or self._sounddevice is None:
            raise RuntimeError("microphone backend is not open")
        frames = max(1, int(self.sample_rate * self.chunk_seconds))
        samples = await asyncio.to_thread(
            self._sounddevice.rec,
            frames,
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="float32",
            device=self.device,
            blocking=True,
        )
        audio = CapturedAudio(
            samples=samples,
            captured_at=datetime.now(timezone.utc),
            sequence=self._sequence,
            sample_rate=self.sample_rate,
            channels=self.channels,
            frames=frames,
        )
        self._sequence += 1
        return audio

    async def close(self) -> None:
        self._opened = False
        self._sounddevice = None


def _contains_raw_audio(value: object) -> bool:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return True
    # NumPy arrays and other media buffers must not be serialized as observations.
    if hasattr(value, "shape") and hasattr(value, "dtype"):
        return True
    if isinstance(value, Mapping):
        return any(_contains_raw_audio(item) for item in value.values())
    if isinstance(value, (tuple, list)):
        return any(_contains_raw_audio(item) for item in value)
    return False
