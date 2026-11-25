# SYLPHY Entity Rework — Stage 3 Complete Overlay

This incremental overlay contains the completed Stage 3 Brain implementation and supporting corrections to World provenance and Runtime worker supervision. It does not contain user databases, runtime state, logs, caches, models, or legacy project data.

## Apply

Extract into the repository root and allow replacement of the listed files. Unlisted files are preserved.

## Verify on Windows

From the repository root, run:

```powershell
.\tools\verify_entity_stage3_windows.ps1
```

The expected final marker is `PASS: Stage 3 Brain verification completed`. The script runs the complete default test suite and compiles the Entity source/test packages.

## Scope boundary

Stage 3 implements deterministic, pure Brain decisions and Intent proposals. It does not authorize or execute actions, persist Brain state across restarts, call an LLM, or connect conversation/voice/vision/avatar capabilities. Runtime wiring and action execution remain in later roadmap stages.
