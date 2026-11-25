# SYLPHY R4 Consolidated Repair

This package resolves the integration mismatch reported after applying the Motion Preview hotfix.

## Why the errors occurred

The Motion Preview hotfix was incomplete as a standalone patch: it replaced `behavior-vrm-runtime.ts` and `main.ts`, which imported/called R4 modules and VrmViewer methods, but did not include the dependent controllers or the matching `vrm-viewer.ts` implementation. Applying it over a base without the complete R4 source caused TS2307 and TS2339.

## Contents

This consolidated patch includes the complete, mutually compatible R4 source set plus the Motion Preview UI:
- `avatar/index.html`
- `avatar/src/main.ts`
- `avatar/src/style.css`
- `avatar/src/vrm-viewer.ts`
- `avatar/src/humanoid-rig.ts`
- `avatar/src/expression-controller.ts`
- `avatar/src/idle-motion.ts`
- `avatar/src/head-gaze-controller.ts`
- `avatar/src/automatic-blink-controller.ts`
- `avatar/src/animation-clip-registry.ts`
- `avatar/src/behavior-vrm-runtime.ts`
- R4 tests and implementation documentation

The VrmViewer in this package exposes `applyPresentationFrame`, `animationClipNames`, `playAnimationClip`, `applyBehavior`, and `setGaze`, matching the Motion Preview UI calls.

## Apply

Extract into the `sylphy_work` repository root and overwrite the matching files. Do not extract into `avatar/src`; preserve the `avatar/` and `docs/` directory structure.

Then run from `avatar/`:

```powershell
npm test
npm run build
npm run dev
```

## Scope boundary

This is a renderer-local preview. It does not establish Entity Daemon IPC, desktop process isolation, or playback-clock lip-sync. No webcam Vision, microphone Listening, or STT is included.
