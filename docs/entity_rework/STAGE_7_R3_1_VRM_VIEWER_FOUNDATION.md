# Stage 7 — R3.1 VRM Viewer Foundation

## Why a separate package

The inspected source snapshot contains no frontend package manager setup and no VRM/GLB/GLTF character asset. R3.1 therefore adds `avatar/` as an isolated TypeScript/Vite package instead of embedding rendering inside the Python Entity process.

## Implemented

- Three.js WebGL renderer with bounded device pixel ratio.
- GLTFLoader with `VRMLoaderPlugin`.
- Local `.vrm` file selection via browser object URL; no upload endpoint.
- VRM scene insertion, model metadata display, expression capability probing.
- Local file validation (extension, non-empty file, 250 MiB size ceiling) with unit tests.
- Orbit controls, automatic model framing from its bounding box, reset camera, unload and resource disposal.
- Render loop calls `vrm.update(delta)` and reports measured FPS.
- Renderer binds to `127.0.0.1`; CSS uses local system fonts without remote font requests.

## Dependencies

- `three@0.180.0`
- `@pixiv/three-vrm@3.5.5`
- Vite + TypeScript

The official three-vrm guide uses GLTFLoader, VRMLoaderPlugin, `gltf.userData.vrm`, scene insertion and `vrm.update(delta)` in the render loop. It also documents compatibility with current Three.js releases. Sources: https://pixiv.github.io/three-vrm/docs/ and https://www.npmjs.com/package/@pixiv/three-vrm.

## Windows commands

```powershell
cd .\avatar
node --version
npm --version
npm install
npm run dev
```

Open `http://127.0.0.1:4173`, select a local `.vrm` file.

Build check:

```powershell
npm run build
```

## Not yet claimed

This development environment did not have Node/npm execution available, so no npm install, TypeScript build, or browser/WebGL test is claimed. The current repository ZIP also contains no VRM model asset. Windows acceptance requires installing dependencies and loading the user's actual VRM file.

This slice does not implement the Entity IPC bridge, semantic animation execution, desktop shell, lip-sync, webcam Vision, or microphone Listening.
