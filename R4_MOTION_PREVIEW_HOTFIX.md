# R4 Motion Preview Hotfix

## Why motion was not visible

The previous R4 package contained procedural motion controllers and behavior preview wiring, but the screenshot shows the pre-R4 inspector UI, so the updated motion controls were not present in the running page. In addition, `BehaviorVrmRuntime` initialized its current behavior to `idle` before calling `applyBehavior('idle')`; the same-state guard returned early and skipped explicit initial state setup.

## Changes

- Adds a dedicated, visible MOTION PREVIEW section with Idle/Breathing, Processing, Reacting, Speaking pose, Interrupted, Pause motion, Resume Idle and Reset gaze controls.
- Reports the current local preview behavior.
- Starts the idle behavior correctly when the VRM runtime is constructed.
- Slightly increases idle breathing/sway amplitude while keeping it subtle.
- Labels the capability as Procedural motion runtime.
- Keeps all behavior preview controls local; no Entity IPC is claimed.

## Apply

Extract the patch at the `sylphy_work` repository root and overwrite the included files.

Then from `avatar/` run:

```powershell
npm test
npm run build
npm run dev
```

Stop any already-running Vite dev server with Ctrl+C before restarting `npm run dev`, then hard-refresh the browser (Ctrl+F5). Load the VRM again. Idle motion starts automatically; use the visible Motion Preview buttons to compare behaviors.

## Acceptance boundary

This hotfix is an integration/UI correction. It does not establish Windows acceptance until the user's `npm test`, `npm run build`, and visual behavior check succeed.
