# SYLPHY Avatar Runtime — R3.1

A standalone, local-first VRM viewer package. It is intentionally separate from the Python Entity daemon.

## Requirements

- Node.js 22 LTS (recommended)
- npm 10+
- A local `.vrm` avatar file (VRM 1.0 preferred)

## Install and run on Windows

Open PowerShell in `sylphy_work\avatar`:

```powershell
node --version
npm --version
npm install
npm run dev
```

Open `http://127.0.0.1:4173`. Select a local `.vrm` file using **Load VRM model**.

Run the local validation tests:

```powershell
npm test
```

Production build and preview:

```powershell
npm run build
npm run preview
```

## Architecture

- `src/vrm-viewer.ts`: Three.js renderer, GLTFLoader + VRMLoaderPlugin, local VRM loading, orbit controls, auto framing, render/update loop and cleanup.
- `src/main.ts`: minimal UI and local file selection lifecycle.
- `src/style.css`: local system-font styling; no remote font or analytics requests.
- `package.json`: pinned compatible runtime dependencies.

`@pixiv/three-vrm` 3.5.5 is paired with Three.js 0.180.0. The official three-vrm usage guide demonstrates `GLTFLoader`, `VRMLoaderPlugin`, reading `gltf.userData.vrm`, adding `vrm.scene`, and calling `vrm.update(delta)` in the render loop.

## Privacy and process boundary

- The selected VRM file is loaded through a browser object URL.
- The model is not uploaded to a server.
- The viewer binds to `127.0.0.1` only.
- No camera, microphone, STT, wake-word, or webcam Vision feature is implemented.
- This package does not start, stop, or own the Python Entity daemon.

## R3.1 acceptance boundary

This slice provides the VRM viewer foundation only. It does not yet provide:
- a VRM asset bundled with the project;
- semantic AnimationCommand -> VRM expression/clip mapping;
- an Entity-to-Avatar IPC transport;
- desktop shell/window integration;
- voice playback clock lip-sync.

The repository snapshot did not contain a `.vrm`, `.glb`, or `.gltf` character asset. A real model must be supplied locally for visual acceptance.


## R5 Windows desktop shell

Electron shell sources are in `desktop/`. Run `npm install`, `npm run desktop:build`, then `npm run desktop`. The desktop window is frameless, transparent, resizable, movable and always-on-top. `Ctrl+Shift+S` toggles visibility; the tray menu restores a hidden window; `Ctrl+Shift+Q` quits. Window bounds are persisted by Electron. The renderer only reads Entity `/status` on loopback and does not control Entity lifecycle. See `docs/entity_rework/STAGE_7_PHASE_5_DESKTOP_EMBODIMENT.md`.

## R7 Entity presentation bridge

The Entity daemon is the sole source of presentation truth. Its durable event projection feeds the typed Avatar adapter, `BehaviorArbiter`, and `BehaviorAnimationMapper`. The latest immutable frame is exposed through read-only loopback `GET /avatar/presentation`. Electron main polls that endpoint and the renderer applies new revisions to the VRM runtime through a narrow preload bridge. The Avatar never submits behavior decisions or controls Entity state.

Voice mouth animation remains a separate read-only projection of `GET /voice/status`, driven by the actual playback clock and amplitude. No microphone, listening, STT, webcam, or Vision dependency is introduced.

Install/build/test on Windows: `npm install`, `npm test`, `npm run desktop:build`; then manage the independent process with `py -3.13 -m sylphy_entity.embodiment.avatar_service start|status|restart|stop`. Entity CLI includes `status`, `world`, `cognition`, `memory`, `voice`, `diagnostics`, `chat`, `history`, `speak`, `voice-stop`, `pause`, `resume`, `permission revoke|restore`, and `avatar presentation|status|start|stop|restart`.
