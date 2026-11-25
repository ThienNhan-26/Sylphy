# Stage 7 — Phase 6: Voice Lip-Sync and Presentation Integration

**Implementation status:** implemented; automated checks recorded in the accompanying R6 report; Windows speaker/Avatar acceptance remains required on the target machine.

## 1. Scope and non-goals

R6 connects the existing Entity Voice Runtime to the existing desktop VRM renderer. The Entity Voice Runtime remains the sole authority for session state and actual playback. The Avatar receives a read-only projection and writes only the VRM mouth/viseme expression channel.

The phase uses the amplitude-based baseline from the Stage 7 remaining roadmap. MFCC/phoneme classification is deliberately deferred unless the amplitude baseline fails the visual quality requirement. This phase adds no webcam, microphone, audio capture, listening, STT, or vision capability.

## 2. Data path

```text
Kokoro TTS -> VoiceAudio -> SoundDeviceOutput callback
                              |
                              +-- first non-empty output block -> playback_started
                              |
                              +-- output frame cursor -> position_seconds
                              |
                              +-- PCM window RMS -> amplitude [0..1]
                                         |
                                         v
VoiceRuntime status + durable VoiceEvent -> Entity loopback API
                                         |
                                         v
Electron main process (read-only GET /voice/status)
                                         |
                                         v
Preload allow-listed getVoiceStatus bridge
                                         |
                                         v
AmplitudeLipSyncController -> VrmViewer.setMouthOpen -> VRM `aa` preset
```

`playback_started` is not emitted when synthesis finishes. It is emitted only after the audio output callback has copied the first non-empty block into the host output buffer. Playback position is derived from the number of output frames consumed, not a timer. RMS is measured over the newly consumed PCM window and bounded to `[0, 1]`.

## 3. Ownership and conflict rules

- Entity owns lifecycle, VoiceSession, sequence, session generation, and speaking state.
- VoiceRuntime is authoritative for `is_speaking`.
- Electron main exposes only the read-only Voice status projection. No Avatar command can start, stop, or mutate Voice or Entity.
- Lip-sync is presentation-only and ignores snapshots from an older `session_generation`, an older per-session event `sequence`, a mismatched session ID, or a backwards playback position. Session generations are scoped to the existing `entity_instance_id`, so a real daemon restart resets the guard safely.
- The lip-sync driver writes only the viseme group through `setViseme`; it does not call `clear()`, so mood and blink weights remain independent.
- Non-playing, completed, interrupted, disabled, and offline status close the mouth.

## 4. Implementation files

| File | Change |
|---|---|
| `src/sylphy_entity/voice/runtime.py` | Actual-start lifecycle, playback clock/amplitude state, session generation, status snapshot |
| `src/sylphy_entity/voice/providers.py` | First-frame start callback, PCM RMS progress, ordered callback dispatch |
| `src/sylphy_entity/voice/contracts.py` | Amplitude in typed VoiceEvent |
| `src/sylphy_entity/voice/integration.py` | Add amplitude to existing Entity event projection; expose runtime status |
| `src/sylphy_entity/runtime/supervisor.py` | Stable disabled-voice status shape and Entity instance identity for generation scoping |
| `avatar/desktop/main.cjs` | Read-only `/voice/status` IPC handler |
| `avatar/desktop/preload.cjs` | Narrow `getVoiceStatus()` bridge |
| `avatar/src/sylphy-desktop.d.ts` | Shared desktop bridge and Voice snapshot types |
| `avatar/src/lip-sync-controller.ts` | Amplitude curve, smoothing, generation/session guards |
| `avatar/src/vrm-viewer.ts` | Mouth-only expression adapter |
| `avatar/src/main.ts` | 50 ms speaking / 180 ms idle read-only status polling |
| `tests/entity_voice/*` | Voice actual-start, progress amplitude and Entity bridge tests |
| `avatar/tests/lip-sync-controller.test.ts` | Mouth gating, release, interruption and stale-session tests |

## 5. Automated acceptance criteria

### Unit

- Mouth remains closed during synthesis/ready.
- Mouth responds to amplitude only when state is `playing` and `is_speaking` is true.
- Completed/interrupted/offline states close the mouth.
- Old generations, old event sequences, same-generation session swaps, and backwards positions are rejected; Entity restart changes the generation scope safely.
- Mood/blink do not get cleared by mouth close.

### Integration

- VoiceRuntime stays `READY` until AudioOutput invokes `on_started`.
- `SoundDeviceOutput` invokes start only after the first real non-empty block and publishes monotonic playback positions with bounded RMS.
- The existing Entity event publisher carries `session_id`, `sequence`, position, duration, and amplitude. No second event bus is created.
- The existing GET `/voice/status` endpoint exposes a read-only snapshot for Electron.

### Failure

- Stop/interruption clears speaking amplitude.
- A new session increments `session_generation`; old session snapshots cannot reopen the mouth.
- Entity-disabled/offline Voice snapshots are treated as closed-mouth presentation even if the daemon generation resets.
- Avatar renderer does not hold or control the Entity process.

### Windows acceptance

Use `tools/verify_r6_voice_lipsync_windows.ps1`. It runs the focused Python and Avatar test/build suites, validates `/voice/status`, and prints the real-speaker visual acceptance steps. A Windows PASS requires the manual spoken playback and interruption checks below.

## 6. Windows manual test

1. Start Entity with the Kokoro model and voices paths already used by the current Windows setup. Confirm `/health` is `ok` and `/voice/status` reports `enabled: true`.
2. Start the Electron Avatar and load the bundled `Sylphy.vrm`.
3. POST one short `/voice/speak` request using the local control token. Before audio starts, state may be `synthesizing` or `ready`; the Avatar mouth must remain closed.
4. While the speaker is playing, `/voice/status` must show `state: playing`, `is_speaking: true`, advancing `position_seconds`, and a changing/bounded `amplitude`. The VRM mouth must visibly move.
5. POST `/voice/stop` during playback. State must become `interrupted`, speaking must become false, amplitude must be zero, and the Avatar mouth must close promptly.
6. Start a second utterance. The new generation must own the mouth; no late progress from the earlier session may reopen it.
7. Close or crash the Avatar and verify Entity `/health` remains `ok` and Voice Runtime remains independent.

PowerShell smoke-test request (the token is read locally; do not paste or share it):

```powershell
$token = (Get-Content "$env:LOCALAPPDATA\SYLPHY\runtime\control.token" -Raw).Trim()
$headers = @{ "X-Sylphy-Control-Token" = $token }
$body = @{
  text = "Hello Sylphy. This is a voice and lip-sync test."
  voice = "af_heart"
  language = "en-us"
  speed = 1.0
} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/voice/speak" -Headers $headers -ContentType "application/json" -Body $body
# During playback, inspect the read-only clock:
Invoke-RestMethod "http://127.0.0.1:8765/voice/status" | ConvertTo-Json -Depth 5
# Interrupt the utterance:
Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/voice/stop" -Headers $headers
```

The sample is an English wiring check; use only a voice/language pair supported by the installed Kokoro assets. No microphone or webcam is used at any point.

## 7. Limits

- Amplitude lip-sync approximates jaw opening; it does not reconstruct phonemes or produce language-aware visemes.
- The renderer polls the local read-only Voice snapshot at 50 ms while speaking and 180 ms while idle. Poll cadence and desktop IPC add bounded presentation latency; playback position itself remains sourced from the output frame cursor.
- The Windows sound-device and real VRM visual acceptance must be run on the user's Windows machine. A successful container/unit test does not substitute for this evidence.
- MFCC/phoneme viseme work is deferred by design and is not required for the Phase 6 baseline.
