# SYLPHY Stage 7 — R7 completion and acceptance report

**Date:** 2026-11-25  
**Source:** supplied `SYLPHY_R7_SOURCE_WITH_GIT.zip` and the included R7 completion candidate / working checkpoint.  
**Result:** R7 implementation candidate completed in one integrated pass. Windows acceptance is deliberately left pending where it requires the owner's physical speaker and interactive desktop.

## 1. Implemented in this pass

- Completed the Entity operator CLI surface:
  - `cognition`, `memory`, `voice`, `diagnostics`
  - `pause`, `resume`
  - `permission revoke|restore [--scope ...]`
  - `avatar presentation|status|start|stop|restart`
- Added authenticated/read-only memory inspection endpoint `GET /memory/status?limit=...`. It returns bounded record metadata and provenance references; it does not expose memory content in the default view or mutate memory.
- Kept cognition inspection as a projection of the existing Entity `/status`; Voice status remains `/voice/status`.
- Fixed the renderer bridge declaration order in `avatar/src/main.ts`. `desktopBridge` was read before its `const` declaration, which TypeScript correctly rejected as a temporal-dead-zone use.
- Preserved the existing Entity-owned event path, Voice lifecycle, behavior arbiter, VRM renderer, Electron process boundary, and Stage 1–6 contracts.
- Added CLI and API regression tests for the new commands and memory status route.

## 2. Automated verification performed in this environment

| Check | Result |
|---|---:|
| Runtime / World / Brain / Execution tests | **160 passed** |
| Persistence tests | **26 passed** |
| Cognition / Memory / Conversation / Sensors / Voice / Embodiment tests | **133 passed** |
| Total Entity tests, run in three isolated groups | **319 passed** |
| Python compileall (`src/sylphy_entity` + relevant tests) | **PASS** |
| Electron `main.cjs` syntax | **PASS** |
| Electron `preload.cjs` syntax | **PASS** |
| TypeScript static build | **BLOCKED** — npm dependencies (`three`, `@pixiv/three-vrm`, Vite/Vitest) were unavailable; global `tsc` confirmed the `desktopBridge` temporal-dead-zone error is gone, while unresolved package imports remain |
| `npm ci` | **BLOCKED** — package installation did not complete before environment timeout; partial `node_modules` was removed |
| Vitest / Vite production build | **NOT RUN** — required npm packages unavailable |
| Ruff | **NOT RUN** — Ruff is not installed in this environment |
| Windows speaker playback | **PENDING** — requires the owner's Windows audio output device and local Kokoro model/voices |
| Electron transparency, tray, shortcuts, drag/resize, crash/restart | **PENDING** — requires interactive Windows desktop |
| Live local Ollama generation | **PENDING** — requires the owner's configured local Ollama model |

The Python test groups above cover all 319 tests configured by the repository. They were run as three groups because a single combined invocation stalled in this execution environment; no group reported a failure. Upstream WebSockets/Uvicorn deprecation warnings were non-failing.

## 3. Windows acceptance — required final gate

From the repository root in PowerShell:

```powershell
py -3.13 -m pip install -e ".[dev,voice-output]"
Set-Location avatar
npm ci
npm test
npm run desktop:build
Set-Location ..
.	oolserify_r7_windows.ps1
```

Then start the Entity with the owner's local Ollama model and Kokoro ONNX asset paths:

```powershell
$env:PYTHONPATH = "$PWD\src"
py -3.13 -m sylphy_entity.daemon --conversation-model <ollama-model> --voice-model <kokoro-model.onnx> --voice-voices <voices.bin>
```

In a second PowerShell window:

```powershell
$env:PYTHONPATH = "$PWD\src"
py -3.13 -m sylphy_entity.interface.cli status
py -3.13 -m sylphy_entity.interface.cli chat "Hello, Sylphy" --session r7-acceptance
py -3.13 -m sylphy_entity.interface.cli speak "Hello. I am here."
py -3.13 -m sylphy_entity.interface.cli avatar start
py -3.13 -m sylphy_entity.interface.cli avatar status
```

Observe that the speaker actually plays, the Avatar begins speaking only after playback starts, mouth motion follows the playback clock, and the Avatar returns to idle after completion. Then run `avatar stop` and verify Entity remains running. Terminate/restart the Avatar process and verify it recovers the current presentation snapshot. Finally run `voice-stop` during speech and verify the mouth closes promptly.

## 4. R7 Definition of Done status

- **Implementation present:** Cognition, Memory, Conversation, Voice Output, Voice–Entity synchronization, Avatar typed events, Behavior Arbiter, VRM runtime, Electron desktop shell, amplitude lip-sync, operator CLI, automated test suite.
- **Automated verification:** Python 319/319 pass in grouped runs; Python compile and Electron syntax checks pass.
- **Target-machine acceptance still required:** actual TTS speaker output, Electron desktop behavior, Avatar process crash/restart on Windows, local Ollama live conversation, and complete text → cognition/memory → voice → Avatar demonstration.
- **Excluded by scope:** webcam Vision, microphone Listening, STT, audio capture, and emotion inference. None is required for R7.

**Release decision:** Do not label Stage 7 Windows acceptance PASS until the target-machine procedure above completes. The implementation work is delivered; the remaining gate is hardware/OS-specific verification that cannot be truthfully simulated from this environment.
