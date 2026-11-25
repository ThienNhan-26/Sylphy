import asyncio
import numpy as np
import pytest
from sylphy_entity.voice.contracts import PlaybackState, VoiceAudio, VoiceEventType, VoiceSession
from sylphy_entity.voice.runtime import VoiceRuntime, VoiceRuntimeError


class FakeTTS:
    def __init__(self, fail=False): self.fail = fail; self.calls = 0
    async def synthesize(self, session):
        self.calls += 1
        if self.fail: raise RuntimeError("tts offline")
        return VoiceAudio(np.zeros(1600, dtype=np.float32), 16000, 0.1)


class FakeOutput:
    def __init__(self, wait=False, start_gate=None):
        self.stopped = False
        self.wait = wait
        self.start_gate = start_gate
        self.entered = asyncio.Event()
        self.started = asyncio.Event()

    async def play(self, audio, *, on_started=None, progress=None):
        self.entered.set()
        if self.start_gate is not None:
            await self.start_gate.wait()
        if on_started:
            await on_started()
        self.started.set()
        if progress:
            await progress(0.05, audio.duration_seconds, 0.25)
        if self.wait:
            while not self.stopped:
                await asyncio.sleep(0.005)

    async def stop(self):
        self.stopped = True


@pytest.mark.asyncio
async def test_speak_emits_ordered_lifecycle_and_completes():
    events=[]; output=FakeOutput(); runtime=VoiceRuntime(tts=FakeTTS(), output=output, event_sink=events.append)
    await runtime.speak(VoiceSession("hello", language="en-us"))
    kinds=[e.event_type for e in events]
    assert kinds == [VoiceEventType.SESSION_CREATED, VoiceEventType.SYNTHESIS_STARTED,
        VoiceEventType.SYNTHESIS_COMPLETED, VoiceEventType.PLAYBACK_STARTED,
        VoiceEventType.PLAYBACK_PROGRESS, VoiceEventType.PLAYBACK_COMPLETED]
    assert [e.sequence for e in events] == list(range(1,7))
    assert runtime.state is PlaybackState.COMPLETED
    assert not runtime.is_speaking
    progress = next(event for event in events if event.event_type is VoiceEventType.PLAYBACK_PROGRESS)
    assert progress.amplitude == pytest.approx(0.25)
    assert runtime.status()["amplitude"] == 0.0


@pytest.mark.asyncio
async def test_playback_started_waits_for_actual_output_start_callback():
    events = []
    gate = asyncio.Event()
    output = FakeOutput(wait=True, start_gate=gate)
    runtime = VoiceRuntime(tts=FakeTTS(), output=output, event_sink=events.append)
    task = asyncio.create_task(runtime.speak(VoiceSession("hello")))
    await output.entered.wait()

    assert runtime.state is PlaybackState.READY
    assert not runtime.is_speaking
    assert not any(event.event_type is VoiceEventType.PLAYBACK_STARTED for event in events)

    gate.set()
    await output.started.wait()
    assert runtime.state is PlaybackState.PLAYING
    assert runtime.is_speaking
    await runtime.stop()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_stop_interrupts_active_playback():
    events=[]; output=FakeOutput(wait=True); runtime=VoiceRuntime(tts=FakeTTS(), output=output, event_sink=events.append)
    task=asyncio.create_task(runtime.speak(VoiceSession("hello")))
    await output.started.wait()
    assert runtime.is_speaking
    await runtime.stop()
    await asyncio.gather(task, return_exceptions=True)
    assert output.stopped
    assert runtime.state is PlaybackState.INTERRUPTED
    assert events[-1].event_type is VoiceEventType.PLAYBACK_INTERRUPTED


@pytest.mark.asyncio
async def test_tts_failure_emits_failure_event_and_surfaces_error():
    events=[]; runtime=VoiceRuntime(tts=FakeTTS(fail=True), output=FakeOutput(), event_sink=events.append)
    with pytest.raises(VoiceRuntimeError, match="tts offline"):
        await runtime.speak(VoiceSession("hello"))
    assert runtime.state is PlaybackState.FAILED
    assert events[-1].event_type is VoiceEventType.PLAYBACK_FAILED


def test_session_rejects_blank_text_and_invalid_speed():
    with pytest.raises(ValueError): VoiceSession("  ")
    with pytest.raises(ValueError): VoiceSession("hello", speed=3.0)

@pytest.mark.asyncio
async def test_output_failure_before_first_device_frame_never_reports_speaking():
    class BrokenOutput:
        async def play(self, audio, *, on_started=None, progress=None):
            raise RuntimeError("device unavailable before first frame")

        async def stop(self):
            return None

    events = []
    runtime = VoiceRuntime(tts=FakeTTS(), output=BrokenOutput(), event_sink=events.append)
    with pytest.raises(VoiceRuntimeError, match="device unavailable"):
        await runtime.speak(VoiceSession("hello"))

    kinds = [event.event_type for event in events]
    assert VoiceEventType.SYNTHESIS_COMPLETED in kinds
    assert VoiceEventType.PLAYBACK_STARTED not in kinds
    assert kinds[-1] is VoiceEventType.PLAYBACK_FAILED
    assert runtime.state is PlaybackState.FAILED
    assert runtime.is_speaking is False
    assert runtime.status()["amplitude"] == 0.0

@pytest.mark.asyncio
async def test_each_new_session_gets_a_new_generation():
    runtime = VoiceRuntime(tts=FakeTTS(), output=FakeOutput())
    first = VoiceSession("first")
    second = VoiceSession("second")
    await runtime.speak(first)
    assert runtime.status()["session_generation"] == 1
    await runtime.speak(second)
    assert runtime.status()["session_generation"] == 2
    assert runtime.status()["active_session_id"] == second.session_id


@pytest.mark.asyncio
async def test_stop_failure_still_cancels_voice_task_and_closes_amplitude():
    class StopErrorOutput:
        def __init__(self):
            self.started = asyncio.Event()

        async def play(self, audio, *, on_started=None, progress=None):
            if on_started:
                await on_started()
            self.started.set()
            await asyncio.Future()

        async def stop(self):
            raise RuntimeError("device abort failed")

    events = []
    output = StopErrorOutput()
    runtime = VoiceRuntime(tts=FakeTTS(), output=output, event_sink=events.append)
    task = asyncio.create_task(runtime.speak(VoiceSession("hello")))
    await output.started.wait()
    await runtime.stop("test_interruption")
    await asyncio.gather(task, return_exceptions=True)

    assert runtime.state is PlaybackState.INTERRUPTED
    assert runtime.is_speaking is False
    assert runtime.status()["amplitude"] == 0.0
    assert events[-1].event_type is VoiceEventType.PLAYBACK_INTERRUPTED
    assert "device abort failed" in events[-1].reason
