"""Cancelable Voice Output orchestration independent of the Entity lifecycle."""
from __future__ import annotations

import asyncio
import math
from typing import Awaitable, Callable

from .contracts import PlaybackState, VoiceEvent, VoiceEventType, VoiceSession
from .providers import AudioOutput, TTSProvider

EventSink = Callable[[VoiceEvent], Awaitable[None] | None]


class VoiceRuntimeError(RuntimeError):
    pass


class VoiceRuntime:
    """Own voice-session lifecycle and publish playback-clock observations.

    ``PLAYING`` is entered only from the audio output's ``on_started`` callback,
    which the concrete output raises after the first non-empty device callback.
    TTS completion alone never means that the speaker has started.
    """

    def __init__(self, *, tts: TTSProvider, output: AudioOutput, event_sink: EventSink | None = None):
        self.tts = tts
        self.output = output
        self.event_sink = event_sink
        self._task: asyncio.Task | None = None
        self._session: VoiceSession | None = None
        self._sequence = 0
        self._session_generation = 0
        self._state = PlaybackState.COMPLETED
        self._position_seconds = 0.0
        self._duration_seconds = 0.0
        self._amplitude = 0.0
        self._lock = asyncio.Lock()

    @property
    def is_speaking(self) -> bool:
        return self._state is PlaybackState.PLAYING

    @property
    def state(self) -> PlaybackState:
        return self._state

    @property
    def active_session_id(self) -> str | None:
        return self._session.session_id if self._session else None

    async def _emit(
        self,
        kind: VoiceEventType,
        *,
        position: float | None = None,
        duration: float | None = None,
        amplitude: float | None = None,
        reason: str | None = None,
    ) -> None:
        if self._session is None:
            return
        self._sequence += 1
        if self.event_sink is None:
            return
        event = VoiceEvent(
            kind,
            self._session.session_id,
            self._sequence,
            self._state,
            position_seconds=self._position_seconds if position is None else position,
            duration_seconds=self._duration_seconds if duration is None else duration,
            amplitude=self._amplitude if amplitude is None else amplitude,
            reason=reason,
        )
        result = self.event_sink(event)
        if asyncio.iscoroutine(result):
            await result

    async def speak(self, session: VoiceSession) -> None:
        async with self._lock:
            if self._task and not self._task.done():
                await self._interrupt_locked("superseded_by_new_session")
            self._session = session
            self._sequence = 0
            self._session_generation += 1
            self._state = PlaybackState.CREATED
            self._position_seconds = 0.0
            self._duration_seconds = 0.0
            self._amplitude = 0.0
            await self._emit(VoiceEventType.SESSION_CREATED)
            self._task = asyncio.create_task(self._run(session), name=f"voice:{session.session_id}")
            task = self._task
        await task

    async def _run(self, session: VoiceSession) -> None:
        try:
            self._state = PlaybackState.SYNTHESIZING
            await self._emit(VoiceEventType.SYNTHESIS_STARTED)

            audio = await self.tts.synthesize(session)
            if self._session is not session:
                return
            self._duration_seconds = audio.duration_seconds
            self._position_seconds = 0.0
            self._amplitude = 0.0
            self._state = PlaybackState.READY
            await self._emit(VoiceEventType.SYNTHESIS_COMPLETED)

            async def playback_started() -> None:
                if self._session is not session or self._state is not PlaybackState.READY:
                    return
                self._state = PlaybackState.PLAYING
                self._position_seconds = 0.0
                self._amplitude = 0.0
                await self._emit(VoiceEventType.PLAYBACK_STARTED)

            async def progress(position: float, duration: float, amplitude: float = 0.0) -> None:
                if self._session is not session or self._state is not PlaybackState.PLAYING:
                    return
                if not all(
                    isinstance(value, (int, float)) and math.isfinite(value)
                    for value in (position, duration, amplitude)
                ):
                    return
                safe_duration = max(0.0, min(float(duration), audio.duration_seconds))
                safe_position = max(self._position_seconds, min(float(position), safe_duration))
                safe_amplitude = max(0.0, min(float(amplitude), 1.0))
                self._position_seconds = safe_position
                self._duration_seconds = safe_duration
                self._amplitude = safe_amplitude
                await self._emit(VoiceEventType.PLAYBACK_PROGRESS)

            await self.output.play(audio, on_started=playback_started, progress=progress)
            if self._state is PlaybackState.READY:
                raise VoiceRuntimeError("audio output completed without reporting playback_started")
            if self._state is PlaybackState.PLAYING:
                self._state = PlaybackState.COMPLETED
                self._position_seconds = audio.duration_seconds
                self._amplitude = 0.0
                await self._emit(VoiceEventType.PLAYBACK_COMPLETED)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self._session is session:
                self._state = PlaybackState.FAILED
                self._amplitude = 0.0
                await self._emit(VoiceEventType.PLAYBACK_FAILED, reason=f"{type(exc).__name__}: {exc}")
            if isinstance(exc, VoiceRuntimeError):
                raise
            raise VoiceRuntimeError(str(exc)) from exc

    async def stop(self, reason: str = "user_requested") -> None:
        async with self._lock:
            await self._interrupt_locked(reason)

    async def _interrupt_locked(self, reason: str) -> None:
        task = self._task
        if task and not task.done():
            self._state = PlaybackState.INTERRUPTED
            self._amplitude = 0.0
            interruption_reason = reason
            try:
                await self.output.stop()
            except Exception as exc:
                # Always cancel the Voice task even if the device abort call
                # fails; AudioOutput.play owns its final stream cleanup.
                interruption_reason = (
                    f"{reason}; audio_output_stop_error={type(exc).__name__}: {exc}"
                )
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self._emit(
                VoiceEventType.PLAYBACK_INTERRUPTED,
                amplitude=0.0,
                reason=interruption_reason,
            )
        self._task = None

    def status(self) -> dict[str, object]:
        """A read-only snapshot for local presentation clients."""
        return {
            "enabled": True,
            "state": self._state.value,
            "is_speaking": self.is_speaking,
            "active_session_id": self.active_session_id,
            "session_generation": self._session_generation,
            "sequence": self._sequence,
            "position_seconds": self._position_seconds,
            "duration_seconds": self._duration_seconds,
            "amplitude": self._amplitude,
        }
