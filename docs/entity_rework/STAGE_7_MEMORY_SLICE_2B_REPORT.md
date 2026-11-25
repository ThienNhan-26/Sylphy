# Stage 7 — Memory Slice 2B Implementation Report

**Date:** 2026-11-24  
**Scope:** Explicit candidate capture, provenance preservation, and controlled confirmation/rejection.

## Implemented

- `contracts/memory.py`: typed `MemoryCandidate`, candidate kind (`user_statement`, `cognition_proposal`, `external_import`), and lifecycle (`pending`, `confirmed`, `rejected`). Candidates remain separate from active `MemoryRecord` objects.
- `memory/repository.py`: `MemoryCaptureService` stages candidates without creating active memories; supports bounded pending listing, explicit rejection, and gated promotion.
- User statements can only be promoted through the user-confirmation path.
- Cognition proposals require both a Brain-review reference and a user-confirmation reference.
- External imports require both a verification reference and a user-confirmation reference.
- Confirmation provenance is appended to the memory's original source references. Promotion of the candidate and insertion of its active memory happen in one SQLite transaction.
- Repeated capture is idempotent for identical candidate IDs/content. Reusing an ID with changed content is rejected. Repeated confirmation returns the already-created memory; rejected candidates cannot be promoted.
- `EntityDatabase` schema v4 adds `entity_memory_candidates` additively. Existing memory and runtime tables are retained.
- `tools/verify_entity_stage7_memory_slice2b_windows.ps1` provides the Windows compile and focused regression acceptance gate.

## Boundaries

- A captured candidate is not a memory and is not available to recall.
- Model output cannot directly write to `entity_memories` through this API.
- No automatic promotion from Cognition, Conversation, or imported legacy data is enabled.
- No Brain/Runtime lifecycle or authority changes; no retrieval/context injection in this slice.
- Legacy memory data remains untouched.

## Container verification

- `python -m compileall -q src/sylphy_entity tests/entity_memory tests/entity_persistence` — passed.
- `tests/entity_memory` + `tests/entity_persistence` — **43 passed**.
- Runtime + World — **53 passed**.
- Brain — **41 passed**.
- Execution — **49 passed**.
- Cognition + Memory + Persistence — **58 passed**.
- Total unique Entity tests passed across isolated groups: **201 passed**. One combined Brain + Execution invocation timed out in the container; both groups passed independently. No one-shot full-suite pass is claimed.
- Windows acceptance is pending.

## Windows acceptance

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\verify_entity_stage7_memory_slice2b_windows.ps1
```

Expected final marker:

```text
PASS: Stage 7 Memory Slice 2B Windows acceptance completed
```

## Next slice

After Windows acceptance, Slice 2C may add controlled retrieval and context assembly. Retrieved memory must remain explicitly labelled as untrusted contextual material, retain provenance, obey bounded retrieval limits, and never become authoritative World state or executable Intent.
