# SYLPHY Stage 7 — R6 Voice Lip-Sync overlay

This is a file overlay for the R5 working repository, not a complete replacement repository.

## Safety

1. Keep the existing `SYLPHY_emergency_20261125_011725.zip` backup. Make a fresh local backup if the working tree has changed since it was made.
2. Do not run `git reset --hard`, `git clean`, or delete repository folders.
3. Close Avatar and gracefully stop Entity before replacing Python/Electron source files. Use Ctrl+Shift+Q for Avatar and Ctrl+C in the Entity daemon console.
4. Extract this ZIP into the repository root `sylphy_work`, preserving the included relative paths. Allow replacement of the listed files only.

## Automated checks on Windows

Start Entity again with the same local Kokoro model and voice pack paths as the existing setup. Keep autonomy disabled. With Entity running, open PowerShell at the repository root:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\tools\verify_r6_voice_lipsync_windows.ps1
```

The script runs the focused Entity Voice tests, Electron JavaScript syntax checks, Avatar TypeScript/Vite build, Avatar Vitest suite, and read-only Entity/Voice API checks. It also prints the required real-speaker test. npm dependencies are not vendored in this patch; use the existing `avatar\node_modules` installation on the Windows machine.

## Real audio / VRM acceptance

Follow section 6 in `docs/entity_rework/STAGE_7_PHASE_6_VOICE_AVATAR_SYNC.md`. It uses the local control token in `%LOCALAPPDATA%\SYLPHY\runtime\control.token`; never copy that token into logs or chat.

R6 is not Windows-accepted until the actual speaker playback, playback clock, VRM mouth movement, interruption close, new-session ownership, and Entity independence are observed on Windows.
