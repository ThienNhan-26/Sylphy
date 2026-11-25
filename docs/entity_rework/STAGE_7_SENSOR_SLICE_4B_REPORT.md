# Stage 7 — Sensor Slice 4B: Camera Adapter

**Status:** Implemented; container verification passed; Windows acceptance pending.

## Roadmap alignment

The Master Plan requires vision/camera and hearing/microphone to be sensor adapters with explicit consent, freshness, privacy scope, and resource claims. Slice 4A established the shared consent and resource lifecycle. Slice 4B adds the first concrete camera capture adapter on top of that boundary.

This slice does not introduce a new daemon, a separate event broker, or a new Entity lifecycle.

## Scope

- Add a replaceable `CameraBackend` protocol and `FrameProcessor` protocol.
- Add an OpenCV backend behind the existing `vision` optional dependency.
- Start the camera only after `SensorAdapterSession.start()` succeeds (consent + exclusive device lease).
- Capture frames in process memory and pass them to a pluggable processor.
- Convert processor output to a bounded JSON-only `Observation`.
- Keep raw frames out of Observation, EntityEvent and EventStore.
- Stop/release camera and resource lease on explicit stop, capture failure, consent loss or session shutdown.
- Provide a metadata-only processor for safe baseline and deterministic tests.

## Architecture

`CameraAdapter -> SensorAdapterSession -> ObservationSink -> existing World/Event path`

The adapter never calls Brain, Cognition, Memory, Intent or execution. It does not own Entity lifecycle. OpenCV is imported only when `OpenCVCameraBackend.open()` is called; importing the package does not open camera hardware.

## Privacy and safety invariants

1. Default deny remains enforced by Slice 4A consent ledger.
2. Consent is checked before opening the physical camera.
3. Camera resource is exclusively claimed through ResourceArbiter.
4. Raw pixel arrays remain ephemeral in process memory.
5. Processor output containing bytes/bytearray/memoryview is rejected before Observation creation.
6. Processor output is bounded to 8 KiB by default and must be JSON serializable.
7. Frame dimensions are bounded by a configurable pixel ceiling (default 1920x1080).
8. Observations use source `sensor:camera:<device_id>`, the granted privacy scope, sequence number and capture timestamp.
9. Stale frames are rejected by the shared sensor session.
10. Camera open/read/processing failure closes the backend and stops/releases the sensor session.
11. Metadata-only processing is not visual understanding; no object recognition, face recognition, or scene interpretation is claimed.

## Files

- `src/sylphy_entity/sensors/camera.py`
- `src/sylphy_entity/sensors/session.py` (read-only public scope property)
- `src/sylphy_entity/sensors/__init__.py`
- `tests/entity_sensors/test_camera_adapter.py`
- `tools/verify_entity_stage7_sensor_slice4b_windows.ps1`
- `pyproject.toml` test discovery includes `tests/entity_sensors`.

## Verification

Container:
- `compileall`: PASS
- `tests/entity_sensors` + World Model + ResourceArbiter: **32 passed**

Windows acceptance:

```powershell
py -3.13 -m pip install -e ".[dev,world,cognition]"
powershell -ExecutionPolicy Bypass -File .\tools\verify_entity_stage7_sensor_slice4b_windows.ps1
```

Expected marker:

```text
PASS: Stage 7 Sensor Slice 4B Windows acceptance completed
```

To use a real OpenCV camera backend later, install the existing optional `vision` extra:

```powershell
py -3.13 -m pip install -e ".[vision]"
```

Physical camera access is not required for the acceptance test; the test suite uses a deterministic fake backend.

## Deferred

- Visual recognition or scene understanding.
- Face identification, biometric inference, or persistent image storage.
- Camera discovery UI and permission prompt UI.
- Microphone / Hearing adapter.
- Automatic Cognition or Memory ingestion from camera observations.
