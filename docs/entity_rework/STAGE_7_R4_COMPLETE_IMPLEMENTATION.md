# Stage 7 — R4 Complete VRM Animation Runtime

## Delivery scope

R4 is delivered as one integrated implementation candidate rather than separate owner-facing R4.1–R4.6 patches. Internal responsibilities remain separated into independently testable modules.

## Implemented

1. **Humanoid rig mapping** — uses VRM normalized humanoid nodes; reports mapped, missing and required bones. Inspection does not mutate the bind pose.
2. **Procedural idle motion** — low-amplitude spine/chest breathing and hip sway driven by render delta time, relative to captured bind pose, with smooth enable/disable and reset.
3. **Head and gaze controller** — clamped yaw/pitch targets, smoothed return, subtle autonomous gaze micro-motion, and neutral reset. Head/neck/eyes have one controller owner to avoid competing transform writes.
4. **Animation clip registry** — registers embedded glTF animation clips, supports explicit semantic aliases, loop/once playback, per-region channels, crossfade, interruption, stop and disposal.
5. **Automatic blink** — deterministic delta-time scheduler; only activates when the loaded model has the VRM blink expression. It does not use webcam or microphone input.
6. **Behavior-to-VRM runtime** — projects the existing Entity behavior vocabulary (`idle`, `processing`, `reacting`, `speaking`, `interrupted`, `paused`, `shutdown`) into local motion, gaze, blink, expression and clip playback.
7. **Presentation-frame adapter** — accepts the renderer-neutral `PresentationFrame` shape produced by the Python `BehaviorAnimationMapper`, including snake_case command fields. Browser event `sylphy:presentation-frame` is the transport seam for a future desktop IPC adapter.
8. **Preview controls** — local behavior buttons, embedded-clip controls and expression controls for manual verification. These do not claim to be a live Entity connection.

## Ownership and safety boundaries

- Entity / Behavior Arbiter remains authoritative for behavior and body-region ownership.
- Renderer executes the received presentation frame; it does not infer Entity truth from LLM text.
- Head/neck/eyes are written by `HeadGazeController`; hips/spine/chest breathing and sway are written by `IdleMotionController`; upper-arm and forearm pose is written by `BodyPoseController`.
- Animation clips take ownership of their active body/head/eyes region channels; procedural controllers yield and resume when the clip channel releases. Semantic behavior clips are assigned to the `body` channel.
- Paused and shutdown stop clip playback, stop breathing/gaze variation and automatic blinking, clear mouth visemes, and retain a static relaxed-arm pose.
- Mouth viseme playback is intentionally not synchronized here. `speaking.lipsync` is a no-op until the later playback-clock lip-sync phase.
- No webcam Vision, microphone Listening, STT or sensor capture is added.
- The custom browser event is a local adapter seam, not proof of daemon-to-browser IPC. Desktop transport is a later system integration responsibility.

## Verification performed during final stabilization

- `tsc -b` — PASS after the final arm-controller changes.
- Direct Three.js runtime smoke test — PASS for the procedural arm solver: both upper arms and forearms leave a horizontal test T-pose; Processing and Reacting settle to distinct profiles; Speaking changes over time; Paused settles without continued drift.
- `npm run build` — TypeScript phase passes. Vite bundling could not be completed in this Linux authoring environment because the supplied `node_modules` contains Windows Rollup native binaries and lacks `@rollup/rollup-linux-x64-gnu`.
- `npm test` — Vitest execution blocked by the same missing Linux Rollup optional native dependency. The unit tests are included but are not claimed as passed.
- Actual browser rendering and visual inspection of `Sylphy.vrm` remain unverified in this environment.

## Windows acceptance

From `avatar/` on the owner's Windows machine:

```powershell
npm test
npm run build
npm run dev
```

Manual preview acceptance:

- Load `Sylphy.vrm` and verify the rig report.
- Leave Idle active for at least 15 seconds; verify subtle breathing/sway, relaxed arms outside the torso silhouette, and automatic blink if the VRM contains the blink preset.
- Compare Processing, Reacting, Speaking, Interrupted, Paused and Resume from front and side views; verify visibly distinct arm/gaze profiles, smooth transitions and no transform snapping.
- Check wrists and hands against the wide sleeves. Any remaining cloth intersection is a model-specific limitation to record, not proof of physical collision handling.
- If the VRM contains glTF animation clips, verify the clip count and use the clip buttons to play them.
- Dispatch a `sylphy:presentation-frame` CustomEvent using a serialized Python `PresentationFrame`; verify semantic commands are consumed.
- Unload and reload the model; verify no stale rig/controller state remains.
- Confirm R3.2 expression controls still work.

## Acceptance limits

This is the complete R4 renderer implementation candidate. Owner Windows test/build and visual acceptance are still required. The current viewer remains a local browser preview; live daemon IPC and desktop process lifecycle are not included in R4. Lip-sync driven by actual Voice playback position remains Phase 6.


## Final arm-pose and behavior differentiation pass

Added and refined `BodyPoseController` to address the T-pose and low-differentiation gaps found during Windows visual review.

- The controller aims both upper-arm and forearm segments using their actual normalized world-space bone directions instead of assuming fixed local axes.
- Idle, Processing, Reacting, Speaking, Interrupted and Paused use separate pose profiles; Speaking includes a small time-varying asymmetric gesture.
- Paused retains a static relaxed-arm pose rather than returning to the imported T-pose.
- Body animation clips use the `body` ownership channel; procedural arm posing yields while that channel is active.
- Capability reporting checks the six upper-arm/lower-arm/hand bones rather than using a mapped-bone count.
- Added unit tests for bilateral arm posing, behavior differentiation, pause stability, clip-channel ownership and reset.

This remains renderer-local arm-pose preview behavior, not a full-body motion library, cloth-collision solver, production mocap system or direct Entity IPC. Outward clearance is a heuristic and may still require tuning for Sylphy’s wide sleeves.
