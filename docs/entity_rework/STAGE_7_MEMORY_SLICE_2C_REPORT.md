# Stage 7 — Memory Slice 2C Implementation Report

**Date:** 2026-11-24  
**Scope:** Bounded active-memory retrieval and provenance-preserving context assembly.

## Implemented

- `memory/recall.py` adds `MemoryRecallService`, `MemoryRecallHit`, and `MemoryContext`.
- Retrieval is deterministic lexical overlap (Unicode NFKC + case-folded tokenization), not embedding search and not an LLM-generated relevance claim.
- Only active `MemoryRecord` rows are eligible. Pending candidates and archived memories are never returned.
- Retrieval is bounded by result count (1–20), scan window (1–500), and rendered context budget (256–20,000 characters).
- Optional category filtering is supported.
- Newest-first bounded scan is exposed by `MemoryRepository.list_active_recent`; the existing `list_active` behavior remains unchanged.
- Every returned item retains memory ID, origin, source references, timestamps, confidence, lexical score, and matched terms.
- Context text is JSON-encoded and explicitly labelled `UNTRUSTED RECALLED MEMORY`; it says the material is not instructions, not current World state, and not authorization to act.
- Oversized items are omitted as whole records; memory content is never silently truncated.
- No automatic prompt injection, Cognition integration, Brain/World update, Intent creation, schema migration, or legacy import is included.

## Architecture boundaries

- Memory remains a recall capability, not the authoritative World Model.
- Retrieved context is reference material only. It cannot grant permission, change Brain state, or create an executable Intent.
- Cognition must explicitly opt in to consuming a `MemoryContext` in a later integration slice; this implementation does not wire it into Ollama requests.
- Retrieval v1 is lexical and bounded. Semantic/vector retrieval is intentionally not claimed or introduced.

## Container verification

- `python -m compileall -q src/sylphy_entity tests/entity_memory tests/entity_persistence` — passed.
- `tests/entity_memory` + `tests/entity_persistence` — **50 passed**.
- Runtime + World — **53 passed**.
- Brain — **41 passed**.
- Execution — **49 passed**.
- Cognition + Memory + Persistence — **66 passed**.
- Entity regression across isolated groups: **209 tests passed** (groups are isolated; no one-shot full-suite pass is claimed).
- Two upstream WebSocket deprecation warnings appeared in persistence/runtime tests.

## Windows acceptance

Run from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\verify_entity_stage7_memory_slice2c_windows.ps1
```

Expected final marker:

```text
PASS: Stage 7 Memory Slice 2C Windows acceptance completed
```

## Next slice

After Windows acceptance, Slice 2D may address memory correction/lifecycle and a deliberately reviewed legacy-import policy. Conversation integration remains a separate Stage 7 item and must not become the parent loop of the Entity.
