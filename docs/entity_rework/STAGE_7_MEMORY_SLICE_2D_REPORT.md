# Stage 7 — Memory Slice 2D Implementation Report

**Date:** 2026-11-24  
**Scope:** Explicit correction, lifecycle history, and reviewed legacy-memory import policy.

## Implemented

- Entity database schema v5 adds `entity_memory_revisions` additively. Existing event, runtime, memory, and candidate tables are retained.
- `MemoryRepository.correct()` snapshots the prior record and updates the current record in one SQLite transaction. The snapshot retains original origin/source provenance; the correction itself requires an explicit `change_ref`.
- Exact active duplicate collisions are rejected. A no-op correction creates no revision. Missing IDs and invalid bounds fail closed.
- `archive()` and `restore()` require explicit change references and record immutable prior-state snapshots. Repeated transitions to the current status are idempotent and do not create duplicate history.
- `history()` returns a bounded chronological revision list. Revisions are not eligible for retrieval; recall continues to query active `entity_memories` only.
- `LegacyMemoryImporter.preview()` opens the legacy database read-only, reads only the allowlisted `memories` table and only rows with `status='confirmed'`, and preserves known session/message provenance without inferring missing sources.
- Legacy preview rows become deterministic `EXTERNAL_IMPORT` pending candidates only when the caller explicitly invokes `stage()`. No import operation writes active memories. Promotion still requires the existing verification and user-confirmation gate.
- Pending/rejected legacy candidates, sessions, messages, cognition tasks, and other legacy tables are not imported. The legacy database is never modified.

## Architecture boundaries

- No direct dependency from `sylphy_entity` to frozen `src/sylphy` modules.
- No automatic migration/import at daemon startup.
- No automatic confirmation of imported candidates.
- No hard-delete API is added; archive/restore and revision history preserve recoverability.
- No Brain, World, Intent, Cognition prompt, or conversation integration is added in this slice.

## Container verification

- `python -m compileall -q src/sylphy_entity tests/entity_memory tests/entity_persistence` — passed.
- Memory + Persistence — **64 passed**, 2 upstream WebSocket deprecation warnings.
- Runtime + World — **52 passed, 1 deselected**; one existing supervisor worker-failure test repeatedly failed in this container because the supervisor remained `RUNNING` after the worker exit. The modified Memory/Persistence modules do not touch this path.
- Brain — **41 passed**.
- Execution — **49 passed**.
- Cognition + Memory + Persistence — **80 passed**.
- Across isolated groups, **222 tests passed**; no one-shot full-suite pass is claimed.

## Windows acceptance

Run from repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\verify_entity_stage7_memory_slice2d_windows.ps1
```

Expected final marker:

```text
PASS: Stage 7 Memory Slice 2D Windows acceptance completed
```

## Next

After Windows acceptance, the Memory workstream is complete. The next Stage 7 workstream is Conversation, which must remain an interaction capability and must not become the Entity Daemon's parent lifecycle loop.
