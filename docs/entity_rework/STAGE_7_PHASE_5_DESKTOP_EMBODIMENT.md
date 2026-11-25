# Stage 7 — Phase 5: Desktop Embodiment (R5)

## Scope and ownership
- Electron is the desktop shell for the existing Vite + Three.js + three-vrm renderer.
- The Entity Daemon remains the sole lifecycle/state authority.
- `avatar_service.py` manages only the Avatar process. It never starts, stops, or owns the Entity.
- The renderer polls the existing read-only `GET /status` loopback endpoint and displays a connection/lifecycle badge. It does not issue Entity commands.
- No webcam, microphone, STT, listening, or image analysis is used.

## Desktop behavior
- Frameless, transparent, resizable, movable, always-on-top BrowserWindow.
- Window bounds persist under Electron `userData/window-state.json`.
- Close control hides the Avatar; tray menu restores it.
- `Ctrl+Shift+S` toggles visibility; `Ctrl+Shift+Q` exits the Avatar process.
- Renderer runs with context isolation, sandbox enabled, and Node integration disabled.
- Preload exposes only four narrow window-management methods.

## Commands (PowerShell)
From repository root:
```powershell
cd avatar
npm install
npm run desktop:build
npm test
cd ..
$env:PYTHONPATH = "$PWD\src"
python -m sylphy_entity.embodiment.avatar_service start
python -m sylphy_entity.embodiment.avatar_service status
python -m sylphy_entity.embodiment.avatar_service restart
python -m sylphy_entity.embodiment.avatar_service stop
```

## Recovery and independence
- Avatar service metadata stores PID + process creation time; PID reuse is rejected.
- Restarting the desktop process restores saved window bounds.
- The Entity process is not a child of Avatar and is not touched by Avatar start/stop/restart.
- The renderer reconnects to Entity by polling `/status`; when Entity is offline it shows `Entity: offline` and keeps rendering.
- This slice does not claim durable restoration of an active voice session or exact in-progress animation. It restores desktop geometry and reconnects to the current Entity status snapshot.

## Acceptance boundary
Automated: Python module compilation, Avatar TypeScript/Vite build, Vitest suite, service start/status/stop on Windows.
Manual Windows evidence required: transparency, topmost, drag/resize, tray/shortcut behavior, Entity survives Avatar termination, Avatar restart restores bounds and reconnects. A script prints these checks but does not mark them PASS without observation.
