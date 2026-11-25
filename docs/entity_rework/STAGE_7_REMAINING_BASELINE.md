# SYLPHY Stage 7 — Remaining-work baseline (R7)

**Baseline source:** `SYLPHY_R7_COMPLETION_CANDIDATE_20261125.zip`, derived from the supplied `SYLPHY_R7_SOURCE_WITH_GIT.zip` working checkpoint.  
**Baseline Git identity:** `master` at `2cdf98d81ca062601dabc25e36edc99af00521bc` (G0 baseline commit).  
**Baseline rule:** Stage 6 remains the accepted foundation; do not rebuild or overwrite it.

## Stage 6 protected baseline

The supplied `SYLPHY_ENTITY_STAGE6_IMPLEMENTATION_REPORT.md` records Windows acceptance on 2026-11-24 with Python 3.13.17, 17 scenario checks passed, and 167 tests passed / 2 skipped. Stage 6's read-only sandbox closed loop, permissions, resource arbitration, evidence journal, recovery, and opt-in autonomy are protected. Stage 7 changes must retain these contracts and regression tests.

## Existing Stage 7 capabilities found in the source

- Cognition: local Ollama adapter, bounded requests, cancellation/timeout handling, durable request/result events, Brain review.
- Memory: durable SQLite records, provenance, revision history, candidate/confirmation gates, bounded lexical recall.
- Conversation: text ingress, local Ollama generation, durable history, idempotency, interruption/recovery, optional Voice Output.
- Voice Output: provider abstraction, local Kokoro ONNX provider, sounddevice speaker output, playback lifecycle, actual playback clock, cancellation and failure reporting.
- Voice/Entity bridge: voice lifecycle facts are published through the existing Entity event ingress.
- Avatar behavior: typed events, priority arbiter, per-region ownership, read-only presentation projection.
- VRM renderer: Three.js + `@pixiv/three-vrm`, humanoid rig mapping, procedural behavior, expressions, blink and amplitude lip-sync.
- Desktop shell: Electron transparent always-on-top window, tray/shortcuts, process manager and read-only Entity bridge.
- CLI: status/world/chat/history/speak/voice-stop/avatar presentation and event publishing existed before this completion pass.

## Remaining integration gaps addressed in this pass

1. Complete the operator CLI surface for cognition, memory inspection, voice status, diagnostics, autonomy pause/resume, permission revoke/restore, and Avatar process start/stop/restart/status.
2. Add a bounded, read-only `/memory/status` projection with provenance metadata and no memory-content disclosure in the default inspection response.
3. Fix a TypeScript temporal-dead-zone error: the desktop bridge was referenced before its `const` declaration in `avatar/src/main.ts`.
4. Record a single R7 implementation/acceptance report and provide a reproducible Windows acceptance procedure.

## Protected and excluded areas

- `src/sylphy/` remains frozen legacy; the new runtime is `src/sylphy_entity/`.
- Stage 1–6 core modules are not redesigned in this pass.
- No webcam Vision, microphone Listening, audio capture, STT, wake-word, or emotion inference is enabled or required.
- Existing camera/microphone legacy adapters are not wired into the Stage 7 default runtime.
- No user database, memory database, local model, VRM asset, or user configuration is deleted by this patch.
