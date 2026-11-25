# Stage 7 — Memory Slice 2A Implementation Report

**Date:** 2026-11-24  
**Scope:** Provenance-first Entity Memory contract and durable repository foundation.

## Implemented

- `src/sylphy_entity/contracts/memory.py`: immutable `MemoryRecord`, declared origin and lifecycle enums, required source provenance, bounded content/category/source references, confidence validation, and timezone-aware timestamps.
- `src/sylphy_entity/memory/repository.py`: Entity-owned SQLite repository with idempotent create by stable memory ID, get, bounded active listing, and explicit archive.
- `src/sylphy_entity/persistence/database.py`: additive schema version 3 migration; no existing Entity persistence tables are rebuilt or removed.
- `tests/entity_memory/`: contract, provenance, idempotency, restart persistence, archive, bounded listing, and v2-to-v3 migration tests.
- `tools/verify_entity_stage7_memory_windows.ps1`: Windows acceptance script for Python 3.13 compile and Memory/Persistence regression.

## Architectural boundaries

- Memory is an experience/semantic recall capability, not authoritative current World state.
- Memory records require one of `user_confirmed`, `cognition_confirmed`, or `imported_verified` origins and at least one source reference.
- No direct imports from `src/sylphy` legacy modules.
- No legacy database import, automatic cognition candidate confirmation, model-generated memory writes, semantic retrieval, ContextBuilder wiring, or Brain/Runtime authority changes in this slice.
- The legacy memory database is preserved untouched. Import requires a later explicit, provenance-audited migration slice.

## Verification

- `python -m compileall -q src/sylphy_entity tests/entity_memory tests/entity_persistence` — passed.
- `PYTHONPATH=/mnt/data/sylphy_current/src python -m pytest tests/entity_memory tests/entity_persistence -q` — **33 passed**.
- Runtime + World — **53 passed**.
- Brain + Execution — **90 passed**.
- Persistence + Cognition + Memory — **49 passed**.
- Full Entity regression therefore passed in isolated groups: **192 passed**. A single combined pytest invocation timed out in the container; no one-shot full-suite pass is claimed.
- Windows acceptance passed on the user's Windows/Python 3.13.17 environment: compile passed and Memory/Persistence regression reported 33 passed, ending with `PASS: Stage 7 Memory Slice 2A Windows acceptance completed`.

## Acceptance command

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\verify_entity_stage7_memory_windows.ps1
```

Expected final marker:

```text
PASS: Stage 7 Memory Slice 2A Windows acceptance completed
```

## Next slice

After Windows acceptance, implement explicit memory capture/provenance flow and controlled handoff from a user-confirmed or Brain-reviewed Cognition proposal. Retrieval/context injection remains a separate slice and must preserve the untrusted-context boundary.
