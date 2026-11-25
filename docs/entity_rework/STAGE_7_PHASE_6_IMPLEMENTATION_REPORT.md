# SYLPHY Stage 7 — R6 Implementation Report

**Scope:** Phase 6 — Voice Lip-Sync and Presentation Integration  
**Baseline:** `SYLPHY_emergency_20261125_011725.zip` (R5 desktop/Entity source archive)  
**Implementation result:** R6 code and focused tests implemented in an overlay patch.  
**Acceptance result:** automated Python/backend checks and the full Avatar TypeScript project compile pass. Vite/Vitest could not be executed in this Linux build container because the restored `node_modules` came from Windows and lacks the Linux Rollup native optional package. Real Windows speaker/VRM acceptance remains pending on the target Windows installation.

## 1. Findings addressed

The baseline VoiceRuntime marked itself `PLAYING` and emitted `voice.playback.started` immediately after TTS synthesis, before the audio output had consumed its first frame. This contradicted the Phase 6 rule that lip-sync starts from actual speaker playback, not TTS completion.

The Avatar had a VRM expression controller with independent mood, blink, and viseme groups, and the bundled `Sylphy.vrm` contains the standard `aa` viseme. The desktop bridge had read-only Entity status but no Voice playback status projection.

## 2. Implemented

1. Added an actual-output-start callback to the AudioOutput contract. `SoundDeviceOutput` announces start only after copying the first non-empty output block into the host callback buffer.
2. Kept playback progress sourced from consumed frame count and added RMS amplitude measured from PCM frames since the preceding progress notification.
3. Extended VoiceEvent and the existing Entity event projection with normalized amplitude. No new event bus was introduced.
4. Extended the existing read-only `/voice/status` projection with playback position, duration, RMS amplitude, sequence, session generation and Entity `instance_id`.
5. Added a read-only Electron IPC handler and one preload method, `getVoiceStatus()`. Avatar does not gain any Voice or Entity control capability.
6. Added `AmplitudeLipSyncController`: closed outside actual playback, amplitude-to-mouth mapping, attack/release smoothing, and guards for stale sequence/session/generation, backwards position, malformed/offline state and Entity daemon restart.
7. Added `VrmViewer.setMouthOpen()` which changes only the VRM viseme group. Mood and blink channels are not cleared or overwritten by the mouth-close operation.
8. Added bounded renderer polling: 50 ms while speaking and 180 ms while idle. The Avatar renderer remains a presentation client.

## 3. Automated evidence

| Check | Result |
|---|---|
| `PYTHONPATH=src pytest -q tests/entity_voice` | **15 passed** |
| Python compile check for `src/sylphy_entity/voice` and `tests/entity_voice` | **PASS** |
| Electron `main.cjs` syntax | **PASS** |
| Electron `preload.cjs` syntax | **PASS** |
| Standalone strict TypeScript compile of `lip-sync-controller.ts` | **PASS** |
| TypeScript syntax parse across Avatar source/tests | **18 files, 0 syntax errors** |
| Standalone Node runtime smoke of lip-sync controller | **6 assertions PASS** |
| Avatar TypeScript project (`tsc -b`) | **PASS** |
| Vite bundle / Vitest | **BLOCKED IN THIS LINUX CONTAINER** — the R5 archive contains Windows `node_modules` and does not include `@rollup/rollup-linux-x64-gnu`; the Windows machine has the matching platform dependency and the supplied acceptance script runs Vite/Vitest there |
| Real Windows speaker playback and VRM mouth observation | **PENDING** — must be observed on the user's Windows machine |

The Python tests cover actual-start gating, progress RMS/clock, completion/interruption/failure, Entity event/status projection, and per-session generation. The Avatar Vitest file adds focused tests for pre-playback silence, amplitude response, stop/release, interruption, stale sequence/generation, malformed/offline snapshots and Entity restart scoping. Its runner could not start in this Linux container because the archive carries Windows-only Rollup binaries; run it via the supplied acceptance script on Windows.

## 4. Windows acceptance gate

Run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\tools\verify_r6_voice_lipsync_windows.ps1
```

Then follow the real-speaker procedure in `STAGE_7_PHASE_6_VOICE_AVATAR_SYNC.md`. The final R6 PASS requires all of these observed on Windows:

- mouth stays closed during synthesis/ready;
- mouth moves only while the speaker is actually playing;
- `/voice/status` position advances and amplitude is bounded;
- interruption sets speaking false/amplitude zero and closes the mouth;
- a new session takes ownership and old progress cannot reopen the mouth;
- Avatar termination does not stop or crash Entity.

## 5. Changed files

- `src/sylphy_entity/voice/contracts.py`
- `src/sylphy_entity/voice/runtime.py`
- `src/sylphy_entity/voice/providers.py`
- `src/sylphy_entity/voice/integration.py`
- `src/sylphy_entity/runtime/supervisor.py`
- `avatar/desktop/main.cjs`
- `avatar/desktop/preload.cjs`
- `avatar/src/sylphy-desktop.d.ts`
- `avatar/src/main.ts`
- `avatar/src/vrm-viewer.ts`
- `avatar/src/lip-sync-controller.ts` (new)
- `tests/entity_voice/test_voice_runtime.py`
- `tests/entity_voice/test_sounddevice_output.py`
- `tests/entity_voice/test_voice_entity_bridge.py`
- `avatar/tests/lip-sync-controller.test.ts` (new)
- `docs/entity_rework/STAGE_7_PHASE_1_ENTITY_VOICE_INTEGRATION.md` (correct read-only/authenticated API distinction)
- `docs/entity_rework/STAGE_7_PHASE_6_VOICE_AVATAR_SYNC.md` (new)
- `tools/verify_r6_voice_lipsync_windows.ps1` (new)
- `R6_PATCH_README.md` (new)

## 6. Remaining limits

Amplitude lip-sync is an approximate jaw-opening signal, not phoneme-accurate viseme reconstruction. MFCC/phoneme work remains conditional per the roadmap and is not a blocker for the amplitude baseline. Real Windows audio-device behavior and actual Avatar visual timing have not been verified in this container. No webcam, microphone, listening, STT, or vision behavior was introduced.
