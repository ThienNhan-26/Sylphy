import asyncio
import sys
import types
import threading

import numpy as np
import pytest

from sylphy_entity.voice.contracts import VoiceAudio
from sylphy_entity.voice.providers import SoundDeviceOutput


class FakeCallbackStop(Exception):
    pass


class FakeOutputStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.aborted = False
        self.closed = False
        self.captured = []

    def start(self):
        self.started = True
        callback = self.kwargs["callback"]
        finished = self.kwargs["finished_callback"]

        def feed_device_callback():
            for _ in range(20):
                block = np.zeros((400, 1), dtype=np.float32)
                try:
                    callback(block, 400, None, None)
                except FakeCallbackStop:
                    self.captured.append(block.copy())
                    break
                self.captured.append(block.copy())
            finished()

        # Real sounddevice callbacks run on an audio thread while the asyncio
        # event loop remains free to dispatch started/progress notifications.
        self.worker = threading.Thread(target=feed_device_callback, daemon=True)
        self.worker.start()

    def stop(self):
        pass

    def abort(self):
        self.aborted = True

    def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_sounddevice_output_reports_position_from_actual_frames(monkeypatch):
    streams = []

    def output_stream(**kwargs):
        stream = FakeOutputStream(**kwargs)
        streams.append(stream)
        return stream

    fake_sd = types.SimpleNamespace(OutputStream=output_stream, CallbackStop=FakeCallbackStop)
    monkeypatch.setitem(sys.modules, "sounddevice", fake_sd)

    samples = np.linspace(-0.5, 0.5, 1000, dtype=np.float32)
    audio = VoiceAudio(samples, 10000, 0.1)
    updates = []
    started = []

    async def on_started():
        started.append(True)

    async def progress(position, duration, amplitude):
        updates.append((position, duration, amplitude))

    output = SoundDeviceOutput(progress_interval_seconds=0.02)
    await output.play(audio, on_started=on_started, progress=progress)

    assert streams[0].started and streams[0].closed
    assert started == [True]
    assert updates
    assert updates[-1][:2] == pytest.approx((0.1, 0.1))
    assert all(a[0] <= b[0] for a, b in zip(updates, updates[1:]))
    assert all(0.0 <= item[2] <= 1.0 for item in updates)
    assert any(item[2] > 0 for item in updates)
    rendered = np.concatenate([block[:, 0] for block in streams[0].captured])[:1000]
    np.testing.assert_allclose(rendered, samples)


def test_sounddevice_output_rejects_invalid_progress_interval():
    with pytest.raises(ValueError):
        SoundDeviceOutput(progress_interval_seconds=0)
