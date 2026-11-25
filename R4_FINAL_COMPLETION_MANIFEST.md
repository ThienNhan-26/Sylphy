# SYLPHY R4 — Final Stabilization Patch

This is the final bounded R4 repair pass based on the Owner-provided `sylphy_work_R4_final.zip` snapshot.

## Problems addressed

1. `TS6133` build blocker: removed the unused `scratchDirection` field.
2. T-pose-only correction: the prior controller rotated the upper arm but left the forearm at its imported bind pose. The revised controller aims both upper-arm and forearm segments using their actual normalized VRM bone directions.
3. Weakly differentiated behaviors: Idle, Processing, Reacting, Speaking, Interrupted and Paused now use distinct arm-pose profiles. Speaking includes a small time-varying asymmetrical arm gesture; Paused holds a stable relaxed-arm pose.
4. Animation ownership: semantic behavior clips now use the `body` channel. The procedural arm controller yields while a body clip owns that channel and resumes after ownership is released. Pause/shutdown restore the static relaxed-arm pose rather than the imported T-pose.
5. Capability reporting: the preview marks arm posing available only when both sides expose upper-arm, lower-arm and hand bones. The UI no longer calls this a full-body pose system.

## Files in this patch

- `avatar/src/body-pose-controller.ts`
- `avatar/src/behavior-vrm-runtime.ts`
- `avatar/src/main.ts`
- `avatar/index.html`
- `avatar/tests/animation-runtime.test.ts`
- `docs/entity_rework/STAGE_7_R4_COMPLETE_IMPLEMENTATION.md`
- `R4_FINAL_COMPLETION_MANIFEST.md`

## Apply

Extract this ZIP into the `sylphy_work` repository root and overwrite matching files. Preserve the directory structure (`avatar/` and `docs/`). No package or lockfile changes are required.

From `avatar/` on Windows:

```powershell
npm test
npm run build
npm run dev
```

Then hard-refresh the preview (Ctrl+F5), load `Sylphy.vrm`, and compare Idle, Processing, Reacting, Speaking, Interrupted and Paused from both front and side views.

## Verification in the authoring environment

- `tsc -b` — PASS after the patch.
- Direct Three.js runtime smoke test of the new arm solver — PASS: both arm segments moved from a horizontal test T-pose to downward/outward targets; Processing and Reacting produced distinct poses; Speaking varied over time; Paused settled without continued drift.
- `npm run build` — TypeScript phase passed, but Vite bundling could not be completed in this Linux environment because the uploaded `node_modules` contains Windows Rollup native binaries and lacks `@rollup/rollup-linux-x64-gnu`.
- `npm test` — not executable in this Linux environment for the same missing Rollup native optional dependency. Do not treat the Vitest suite as passed until it runs on Windows.
- Browser rendering with the actual `Sylphy.vrm` has not been verified here. Owner visual acceptance is still required.

## Scope and limitations

This is a procedural arm-pose/gesture pass, not a full-body animation library. It does not implement walking, sitting, hand-object interaction, or physical cloth/body collision. The outward arm clearance is a model-agnostic heuristic; the wide sleeves and arm geometry in `Sylphy.vrm` may still require avatar-specific tuning or a model-side adjustment. No Entity IPC, desktop embodiment, webcam Vision, microphone Listening, STT or playback-clock lip-sync is added by this patch.

R4 can be closed only after the Windows test/build commands pass and the owner confirms that the actual VRM has acceptable arm placement and meaningful behavior differences.
