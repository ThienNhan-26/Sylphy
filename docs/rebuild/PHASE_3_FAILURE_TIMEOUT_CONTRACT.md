# SYLPHY Phase 3 — Failure and Timeout Contract

**Date:** 2026-11-27  
**Evidence basis:** Phase 3 implementation/audit reports, focused tests, and source excerpts reviewed with the Project Owner.

## 1. Governing rules

- Provider failure must not be represented as successful cognition.
- Structured model output is parsed and validated against the typed Cognition contract.
- Evidence references must be drawn from the request's supplied source references.
- Cognition output is evidence/proposal material only; it does not grant permissions or execute actions.
- Cancellation and shutdown must preserve durable state and must not falsely mark an unclean shutdown as clean.
- Retry, where present, must remain bounded and must not create duplicate side effects.

## 2. Failure categories and evidence status

| Failure category | Contract / observed behavior | Evidence status |
|---|---|---|
| Provider unavailable / connection refused | Provider failure is represented as a failed outcome rather than successful cognition | Focused adapter tests/reports; exact complete matrix not reproduced here |
| Request timeout | Bounded request timeout; timeout has a distinct status | Adapter implementation/tests reported |
| Request cancellation | Cancellation is represented distinctly; task cancellation during provider execution is preserved | Focused tests and P3-4 reliability report |
| Malformed JSON | Invalid output, not success | Focused tests |
| Structured schema mismatch | Invalid output; typed validation rejects invalid payload | Focused tests |
| Unknown evidence reference | Rejected as invalid output | Focused tests |
| Output truncation | Identified in the Phase 3 handover contract; no complete live truncation acceptance result supplied here | Partially evidenced / not fully verified |
| Model server restart / inference interruption | Identified in the handover failure contract; no exhaustive restart/interruption acceptance matrix supplied here | Partially evidenced / not fully verified |
| Resource unavailable | Resource-busy path is covered in P3-4 reliability tests | Test evidence reported |
| Startup failure | Cleanup path closes Conversation runtime and guards cognition cleanup | Focused startup cleanup tests/reports |
| Cognition close failure | Supervisor logs failure, withholds clean-shutdown marker, transitions to ERROR, and completes terminal cleanup | `test_shutdown_failure.py` supplied and inspected |
| Repeated Cognition close | `LlamaCppCognition.close()` returns immediately after `_closed` is set | Source inspected |

## 3. Cancellation and journal recovery

P3-4 reliability review recorded two cancellation edges:
- **C1 — cancellation before coroutine starts:** the request remains journaled without an in-process result/receipt; restart replay repairs the stale request. No provider call occurs.
- **C3 — cancellation during result publication:** the result is durable but the immediate receipt may be absent; replay restores exactly one receipt.

These are recoverable journal/replay cases, not evidence of lost durable state.

## 4. Shutdown behavior

The Cognition close-failure test verifies:
- provider close failure is logged and not silently treated as success;
- clean-shutdown marking is withheld;
- lifecycle becomes `ERROR`;
- shutdown completion is signaled;
- instance lock and runtime token are released;
- database connection is closed;
- repeated Supervisor shutdown is a no-op.

The daemon also invokes Cognition close after Supervisor shutdown. `LlamaCppCognition.close()` is idempotent. If its underlying client close fails after `_closed` is set, a subsequent close does not retry; the system records an unclean shutdown rather than claiming successful cleanup.

## 5. Verification boundary

This document records the tested and reported behaviors above. It does not claim that every failure category in the Phase 3 handover has been exercised in one exhaustive end-to-end Windows matrix. Truncation, server restart, and all interruption permutations remain incompletely evidenced in the material reviewed.
