# Stage 7 — R4.1 Humanoid Bone Mapping

## Scope
R4.1 inspects the VRM normalized humanoid skeleton and exposes a diagnostic report to the Avatar Preview. It intentionally does not animate or modify bone transforms.

## Implementation
- `avatar/src/humanoid-rig.ts` maps normalized humanoid nodes for hips, spine, chest, upper chest, neck, head, eyes, jaw, shoulders, arms, hands, legs, feet and toes.
- A usable full-body rig requires hips, spine, head, both upper/lower legs and feet, and both upper/lower arms and hands. Eyes, jaw, chest, shoulders and toes are reported but optional for this slice.
- The report includes mapped/total counts, missing bones, critical missing bones, parent names and hierarchy depth.
- `VrmViewer` creates the rig mapping on successful VRM load and clears it on unload.
- Preview displays Humanoid Bone Mapping status and mapped bone count.
- Tests cover mapping, missing-bone reporting, hierarchy metadata, transform non-mutation and refresh behavior.

## Boundary
Normalized humanoid nodes are used for model-independent mapping. This slice only discovers and reports nodes; R4.2/R4.3 will own motion controllers. No direct Entity IPC, camera, microphone, STT or webcam Vision is added.

## Windows acceptance
From `avatar/`: `npm test`, `npm run build`, `npm run dev`. Load `Sylphy.vrm`; confirm the Humanoid Bone Mapping indicator turns green and mapped count is displayed. Count is model-dependent and must not be hardcoded.
