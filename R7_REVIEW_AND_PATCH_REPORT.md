# SYLPHY R7 — checkpoint review and integration patch

Date: 2026-11-25

## Baseline

- Source: `SYLPHY_R7_WORKING_CHECKPOINT_20261125_2.zip`
- Branch in the archive: `master`
- Git HEAD in the archive: `2cdf98d81ca062601dabc25e36edc99af00521bc` (G0 baseline commit)
- The archive contained a working tree with the later Stage 1–7 implementation as untracked/modified files relative to that baseline. Those files were preserved; this patch does not reset or replace the existing work.

## Changes in this review

1. Fixed source-checkout worker imports when sandbox subprocesses execute with the sandbox as `cwd`.
2. Connected durable Entity events to the typed Avatar event adapter, behavior arbiter, and presentation mapper.
3. Exposed the latest Entity-owned presentation frame through read-only `GET /avatar/presentation`.
4. Connected the Electron main process and renderer to poll/apply presentation revisions through a narrow preload bridge.
5. Added `sylphy-avatar` process-manager entry point.
6. Added Entity CLI commands for text conversation, history, Voice Output, Voice stop, and Avatar presentation inspection.
7. Added tests and a repeatable Windows R7 verification script.

## Verification results

| Check | Result |
|---|---:|
| Python regression suite (`PYTHONPATH=src pytest -q`) | 311 passed |
| Python compileall | PASS |
| Electron main/preload JavaScript syntax checks | PASS |
| Avatar Vitest | NOT RUN — npm dependencies unavailable |
| Avatar TypeScript/Vite production build | NOT RUN — npm dependencies unavailable |
| Real Windows speaker playback | NOT RUN — requires target Windows audio device |
| Electron transparency/tray/shortcut/drag behavior | NOT RUN — requires interactive Windows desktop |
| Avatar crash/restart and Entity independence | Automated service code present; target Windows acceptance pending |
| Local Ollama live inference | Not run; integration-marked test is excluded by default |

## Run on Windows

From the extracted repository root in PowerShell:

```powershell
py -3.13 -m pip install -e ".[dev,voice-output]"
cd avatar
npm install
cd ..
.\tools\verify_r7_windows.ps1
```

Then perform the manual Windows checks reported by the script. Start Entity with the intended local model and Kokoro ONNX paths, and start the Avatar separately:

```powershell
$env:PYTHONPATH = "$PWD\src"
py -3.13 -m sylphy_entity.daemon --conversation-model <ollama-model> --voice-model <kokoro-model.onnx> --voice-voices <voices.bin>
```

In a second PowerShell window:

```powershell
$env:PYTHONPATH = "$PWD\src"
py -3.13 -m sylphy_entity.embodiment.avatar_service start
py -3.13 -m sylphy_entity.interface.cli chat "Hello, Sylphy" --session default
py -3.13 -m sylphy_entity.interface.cli speak "Hello. I am here."
py -3.13 -m sylphy_entity.interface.cli avatar
```

Do not mark Windows acceptance PASS until real playback and desktop lifecycle checks have been observed. Webcam, microphone, listening, STT, and emotion inference remain outside this Stage 7 scope.


## Final R7 one-pass completion update — 2026-11-25

The completion pass added the remaining operator controls and memory inspection surface, and fixed a renderer startup compile defect.

- `sylphy-entityctl cognition|memory|voice|diagnostics`
- `sylphy-entityctl pause|resume`
- `sylphy-entityctl permission revoke|restore [--scope ...]`
- `sylphy-entityctl avatar presentation|status|start|stop|restart`
- Read-only bounded `GET /memory/status`
- Fixed `desktopBridge` declaration order before its first use in `avatar/src/main.ts`.

Verification after this update:
- Runtime/World/Brain/Execution: 160 passed.
- Persistence: 26 passed.
- Cognition/Memory/Conversation/Sensors/Voice/Embodiment: 133 passed.
- Total: 319 passed across three test groups.
- Python compileall and Electron main/preload syntax checks: PASS.
- TypeScript/Vitest/Vite remain blocked by missing npm packages in this environment; `npm ci` timed out and partial `node_modules` was removed.
- Physical Windows speaker and interactive desktop acceptance remain pending. See `docs/entity_rework/STAGE_7_R7_COMPLETION_REPORT.md`.
