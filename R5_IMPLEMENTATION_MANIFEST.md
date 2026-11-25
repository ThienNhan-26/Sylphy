# SYLPHY R5 — Desktop Embodiment Implementation Manifest

Input baseline: uploaded `sylphy_work_R5.zip`.

## Implemented
- Electron 37 desktop shell around the existing Vite/Three.js/VRM renderer.
- Frameless transparent, resizable, movable, floating always-on-top window.
- Persistent window bounds in Electron userData.
- Tray restore, Ctrl+Shift+S visibility toggle, Ctrl+Shift+Q explicit quit.
- Narrow context-isolated preload bridge for window controls and read-only Entity status.
- Entity status projection over loopback `GET /status`; Avatar never controls Entity lifecycle.
- Bundled `Sylphy.vrm` public asset auto-load in desktop mode; browser preview retains manual file selection.
- Python Avatar process manager with PID + process creation-time identity validation, start/stop/restart/status. It manages Avatar only.
- Windows acceptance PowerShell script.
- Documentation and focused Python tests.

## Changed / added
- `avatar/package.json`
- `avatar/desktop/main.cjs`
- `avatar/desktop/preload.cjs`
- `avatar/src/sylphy-desktop.d.ts`
- `avatar/src/bridge/entity-client.ts`
- `avatar/src/main.ts`
- `avatar/index.html`
- `avatar/public/Sylphy.vrm`
- `avatar/README.md`
- `src/sylphy_entity/embodiment/avatar_service.py`
- `tests/entity_embodiment/test_avatar_service.py`
- `tools/verify_r5_windows.ps1`
- `docs/entity_rework/STAGE_7_PHASE_5_DESKTOP_EMBODIMENT.md`

## Validation performed in build container
- Python module compile: PASS.
- Focused Avatar service tests: 3 passed.
- Electron main/preload syntax: to be run with Node check in current package validation.
- Vite/TypeScript build: NOT RUN successfully because the clean source archive excludes `node_modules`; the attempted build reported missing `three` and `@pixiv/three-vrm`. This is an environment/dependency-install prerequisite, not a successful build.
- Windows native window behavior: NOT VERIFIED in this Linux build container.

## Windows first-run
`avatar/package-lock.json` belongs to the baseline and does not yet contain Electron. Run `npm install` (not `npm ci`) once in `avatar`; this resolves Electron and updates the lockfile. Then run `npm run desktop:build`, `npm test`, and `tools/verify_r5_windows.ps1`.

## Explicit limits
- The renderer reads Entity `/status` only. Typed event streaming / presentation-frame delivery is not added by this R5 desktop slice; existing Entity presentation contracts remain unchanged.
- Process manager starts/stops/restarts the Avatar shell only. It does not supervise Entity.
- Window bounds are restored. An in-progress voice session or exact animation phase is not restored.
- Transparency, topmost behavior, tray UX, drag/resize, and crash recovery require Windows acceptance evidence.
- No camera, microphone, STT, listening, or webcam Vision is used.
