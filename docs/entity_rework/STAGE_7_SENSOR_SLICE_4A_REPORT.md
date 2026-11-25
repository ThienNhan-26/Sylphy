# Stage 7 — Sensor Slice 4A Foundation Report

**Status:** Implemented; container verification passed; Windows acceptance pending.

## Roadmap alignment

The governing Master Plan orders Stage 7 as Cognition, Long-term Memory, Conversation, then Vision/camera and Hearing/microphone. Conversation is now accepted on Windows: a user message becomes one durable Entity Event and its bounded reference is projected into WorldSnapshot. The next official roadmap item is therefore the sensor-adapter boundary. This slice does not add an unlisted assistant turn-orchestration campaign.

## Scope

Slice 4A establishes the shared safety and lifecycle foundation required by both future camera and microphone adapters. It does not open hardware, capture real media, perform image recognition, transcribe audio, or persist raw media.

## Added components

- `contracts/sensor.py`: `SensorKind` and immutable `SensorConsentGrant`, scoped by sensor, device, purpose, grantor and optional expiry.
- `sensors/consent.py`: fail-closed, in-memory `SensorConsentLedger`; grants are not restored across daemon restarts.
- `sensors/session.py`: `SensorAdapterSession` validates consent, source, exact privacy scope, observation freshness and JSON-only payloads.
- `ResourceArbiter.renew(...)`: atomically renews still-active owned leases.
- Sensor sessions claim an exclusive resource key (`sensor:<kind>:<device_id>`) and renew it while active. Revocation or lease loss stops the session and releases the claim.
- `tests/entity_sensors/`: tests for default deny, consent, expiry, revocation, privacy scope, freshness, raw-media rejection, exclusive device claims and lease renewal.
- `tools/verify_entity_stage7_sensor_slice4a_windows.ps1`: Windows compile and focused regression acceptance.

## Invariants

1. No consent means no sensor session.
2. Consent is tied to one sensor, one device, one purpose and one privacy scope.
3. Consent is ephemeral in 4A; daemon restart returns to deny-by-default.
4. Device resource ownership is exclusive and renewable; stop/revoke releases it.
5. Stale observations, future timestamps, wrong source IDs and scope mismatches are rejected.
6. Raw binary media cannot enter the shared Observation/EventStore JSON path.
7. Importing the sensors package does not open camera or microphone hardware.
8. Sensors remain adapters; they do not own Brain, Runtime, World or Entity lifecycle.

## Verification

Container:

```text
compileall: PASS
tests/entity_sensors tests/entity_world/test_world_model.py tests/entity_execution/test_resource_arbiter.py
26 passed
```

Windows acceptance:

```powershell
py -3.13 -m pip install -e ".[dev,world,cognition]"
powershell -ExecutionPolicy Bypass -File .\tools\verify_entity_stage7_sensor_slice4a_windows.ps1
```

Expected marker:

```text
PASS: Stage 7 Sensor Slice 4A Windows acceptance completed
```

## Deferred

- Camera device discovery/open/capture adapter.
- Microphone device discovery/open/capture adapter.
- Vision inference and audio transcription.
- Persistent consent management UI.
- Automatic cognition or memory injection from sensor outputs.
- Voice output, Avatar, Presentation and CLI/UI integration.
