# Stage 7 — Sensor Slice 4C: Microphone Adapter

**Status:** Implemented; container verification passed; Windows acceptance pending.

## Roadmap alignment

Slice 4A established explicit sensor consent, freshness, privacy scope and exclusive renewable resource leases. Slice 4B added the camera adapter. Slice 4C adds the corresponding microphone adapter, using the same lifecycle boundary and without introducing a new Entity lifecycle or event bus.

## Scope

- Add `MicrophoneBackend` and `AudioProcessor` protocols.
- Add optional `SoundDeviceMicrophoneBackend` using the existing `voice` extra.
- Start microphone hardware only after `SensorAdapterSession.start()` has validated consent and acquired the exclusive device lease.
- Keep captured audio samples ephemeral in process memory.
- Require bounded JSON-only processor output; reject raw bytes, bytearrays, memoryviews and array-like buffers before Observation creation.
- Enforce maximum chunk duration and payload size.
- Release backend and device lease on stop, open/read/processing failure or session termination.
- Add `AudioMetadataProcessor` as deterministic baseline; it emits sample rate, channels, frame count, duration and RMS level only.

## Architecture

`MicrophoneAdapter -> SensorAdapterSession -> ObservationSink -> existing World/Event path`

The adapter does not call Brain, Cognition, Memory, Intent or execution. It does not own Entity lifecycle. Importing the module does not open the microphone; `sounddevice` is imported only when the optional backend is explicitly opened.

## Safety invariants

1. No explicit microphone consent means no hardware open.
2. Consent is bound to microphone device, purpose and permission scope.
3. Device resource ownership is exclusive and renewable through ResourceArbiter.
4. Raw audio samples remain ephemeral and are never included in Observation/EventStore.
5. Processor output containing raw media buffers is rejected.
6. Processor output is JSON serializable and bounded to 8 KiB by default.
7. Audio chunks exceeding the configured duration (10 seconds by default) are rejected.
8. Observation provenance includes source ID, sequence, capture timestamp and permission scope.
9. Open/read/processor failure stops the session and releases the device resource.
10. Baseline RMS metadata is not speech recognition, transcription, speaker identification or semantic hearing.

## Files

- `src/sylphy_entity/sensors/microphone.py`
- `src/sylphy_entity/sensors/__init__.py`
- `tests/entity_sensors/test_microphone_adapter.py`
- `tools/verify_entity_stage7_sensor_slice4c_windows.ps1`
- `SYLPHY_ENTITY_REWORK_MASTER_PLAN.md`

## Verification

Container:
- `compileall`: PASS
- `tests/entity_sensors` + World Model + ResourceArbiter: **38 passed**

Windows acceptance:

```powershell
py -3.13 -m pip install -e ".[dev,world,cognition]"
powershell -ExecutionPolicy Bypass -File .\tools\verify_entity_stage7_sensor_slice4c_windows.ps1
```

Expected marker:

```text
PASS: Stage 7 Sensor Slice 4C Windows acceptance completed
```

No physical microphone is required; tests use a deterministic fake backend.

To use a physical microphone later, install the existing optional voice extra:

```powershell
py -3.13 -m pip install -e ".[voice]"
```

## Deferred

- Speech-to-text / Whisper integration.
- Voice activity detection and turn segmentation.
- Speaker identification.
- Automatic Cognition or Memory ingestion from audio.
- Voice output, Avatar, Presentation and CLI/UI.


## Corrective fix — cleanup visibility race

The first Windows acceptance attempt reported 37 passed / 1 failed in the duration-limit test. The adapter closed its backend, but `_capture_loop` set `_task = None` before awaiting `SensorAdapterSession.stop()`. The test observed `running == False` and inspected ResourceArbiter during the still-pending cleanup, exposing a race in lifecycle visibility.

The implementation now keeps the adapter task visible as running until backend close and session stop have completed. `_task` is cleared only after the lease-release path finishes. The same ordering was applied to CameraAdapter for lifecycle consistency.

After the fix:
- Compile: PASS.
- Sensor + World + ResourceArbiter regression: **38 passed**.
- Duration-limit failure test: **8 consecutive passes** before the container's external execution limit interrupted the additional repetition loop; the complete 38-test group passed separately.
- Windows acceptance remains pending and must be rerun using the updated patch.
