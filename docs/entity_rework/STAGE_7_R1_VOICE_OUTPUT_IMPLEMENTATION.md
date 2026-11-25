# Stage 7 — R1 Voice Output Runtime (implementation slice)

## Scope

This slice adds a local TTS output port and a cancelable playback lifecycle. It does not capture microphone audio, run STT, listen for wake words, or depend on camera input. It does not make Avatar or Voice the Entity lifecycle authority.

## Files

- `src/sylphy_entity/voice/contracts.py`: VoiceSession, VoiceAudio, PlaybackState and typed lifecycle events.
- `src/sylphy_entity/voice/providers.py`: TTSProvider/AudioOutput ports, lazy Kokoro ONNX provider and sounddevice output adapter.
- `src/sylphy_entity/voice/runtime.py`: orchestration, event sequencing, cancellation, interruption and failure propagation.
- `tests/entity_voice/`: fake-backed Voice Runtime and output-stream clock tests; no speaker or TTS model required.
- `pyproject.toml`: separates `voice-output` from frozen `voice-input-legacy` dependencies.

## Install on Windows

From the repository root, in the active virtual environment:

```powershell
python -m pip install -e ".[dev,voice-output]"
```

The Kokoro provider requires the ONNX model and voice pack files to be downloaded separately and supplied by explicit local paths. This patch intentionally does not bundle model weights or assume a model location.

## Run tests

```powershell
$env:PYTHONPATH = "src"
python -m pytest -q tests/entity_voice
```

## Minimal integration example

```python
from sylphy_entity.voice import VoiceRuntime, VoiceSession
from sylphy_entity.voice.providers import KokoroOnnxProvider, SoundDeviceOutput

voice = VoiceRuntime(
    tts=KokoroOnnxProvider(r"D:\\AI\\TTS\\kokoro-v1.0.onnx", r"D:\\AI\\TTS\\voices-v1.0.bin"),
    output=SoundDeviceOutput(),
    event_sink=publish_voice_event,  # bridge to the EXISTING Entity event ingress
)
await voice.speak(VoiceSession("Hello. I am Sylphy.", voice="af_heart", language="en-us"))
```

`publish_voice_event` is an integration callback, not a new event bus. The caller must adapt the typed `VoiceEvent` into the repository's existing Entity event ingress. That wiring is intentionally a later integration step after reviewing the daemon/supervisor event ownership.

## Current limitations / next work

1. `SoundDeviceOutput` now uses `sounddevice.OutputStream` callback playback. Playback position is derived from frames copied into the output stream, and progress is reported at a configurable interval (50 ms by default). This is a device-stream frame clock, not a wall-clock estimate.
2. The callback adapter is covered by a fake sounddevice stream test that verifies monotonic position, final position, and sample delivery.
2. The first Kokoro adapter is an offline provider with explicit model paths; Windows device selection, output sample format, resampling, and real Vietnamese voice quality/performance still require hardware acceptance.
3. This patch does not claim Windows audio acceptance. Only fake-backed unit tests were run here; no physical Windows speaker test has been performed.
4. The optional `voice-input-legacy` extra preserves prior STT/VAD dependency availability but is not part of the Voice Output install path and must not be imported by `sylphy_entity.voice`.
