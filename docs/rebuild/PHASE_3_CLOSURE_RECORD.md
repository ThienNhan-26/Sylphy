# SYLPHY Phase 3 — Closure Evidence Record

**Record date:** 2026-11-27  
**Governing roadmap:** `SYLPHY_MASTER_PLAN_FUTURE_HANDOVER_v3.0.md`, Section 9  
**Execution handover:** `SYLPHY — MASTER HANDOVER & PHASE 3 ROADMAP`, Version 2.0, 2026-11-26  
**Baseline:** GitHub `main` after Phase 2 closure, merge commit `a33a829690246a3448cddd85c7dc68854e144348`  
**Local rebuild:** `D:\CWDI\SYLPHY_CWDI\Sylphy-main` (ZIP extraction; no Git history, remote, refs, or reflog)  
**Status:** IMPLEMENTATION AND REGRESSION PASS; CLOSURE CANDIDATE; OWNER ACCEPTANCE PENDING

---

## 1. Decision summary

Phase 3 implementation, focused tests, Windows runtime integration evidence, and reliability regression evidence are recorded as passing within the inspected scope.

This record does **not** declare unconditional Phase 3 completion. The original Phase 3 exit gate also requires measured Vietnamese quality and recorded resource/latency limits. The evidence currently available is partial for those items. Comparative model/provider benchmarking was explicitly excluded by the Project Owner's direction to keep Gemma 4 Artemis and llama.cpp fixed; this is recorded as an owner-directed scope deviation, not as a completed benchmark.

**Recommended disposition:** accept the implementation and regression evidence with recorded limitations, then obtain explicit Project Owner acceptance of the documented deviations and remaining evidence gaps before marking Phase 3 fully closed.

## 2. Owner direction and fixed runtime

The following decisions govern this Phase 3 implementation:

- Conversation model: **Gemma 4 Artemis**, file `gemma-4-E2B-it-Artemis-Q3_K_M.gguf`.
- Conversation provider: **llama.cpp**.
- Cognition provider for the intended runtime: **llama.cpp**.
- Local endpoint: `http://127.0.0.1:9931`.
- Cognition inherits the Conversation model; the normal runtime does not require `--cognition-model`.
- No model/provider experimentation or comparative provider/model selection is in scope.
- Ollama code remains as a legacy compatibility path; it is not the default runtime path.
- No Phase 4 work is included or authorized by this record.

## 3. Baseline and provenance

The GitHub `main` snapshot after Phase 2 is the comparison baseline:

- Merge commit: `a33a829690246a3448cddd85c7dc68854e144348`.
- Phase 2 closure: `docs/rebuild/PHASE_2_CLOSURE_RECORD.md`, dated 2026-11-26, decision **ACCEPTED WITH RECORDED LIMITATIONS**.
- Phase 2 Windows verification recorded in that closure: Python 3.13.17, pytest 9.2.0, **392 passed, 2 skipped, 0 failed**.

The local Phase 3 working directory was extracted from a ZIP archive and has no Git commit history, remote, refs, or reflog. This is consistent with ZIP extraction and is not itself a source defect.

Targeted Git blob comparisons against the Phase 2 baseline produced:

| File | Result |
|---|---|
| `src/sylphy_entity/cognition/ollama.py` | Exact blob match |
| `tests/entity_runtime/test_llamacpp_adapter.py` | Exact blob match |
| `src/sylphy_entity/cognition/__init__.py` | Changed; exports `LlamaCppCognition` |
| `src/sylphy_entity/daemon.py` | Changed; adds llama.cpp Cognition selection, model inheritance and shared inference slot |
| `docs/rebuild/PHASE_2_CLOSURE_RECORD.md` | Not present at the checked local path; present on GitHub baseline |

These are targeted comparisons only. They do not establish byte-for-byte identity of the entire local source tree or prove the exact ZIP archive hash.

## 4. Acceptance evidence matrix

| ID | Requirement / area | Evidence | Assessment |
|---|---|---|---|
| P3-A01 | Cognition interface remains provider-independent | Existing interface retained; adapter injected at Entity boundary | PASS — documented/code-inspected |
| P3-A02 | llama.cpp Cognition adapter | `LlamaCppCognition` implementation and focused tests reported | PASS — tests and runtime evidence |
| P3-A03 | Brain remains outside provider implementation | Cognition result is evidence/proposal input; Brain/Runtime retain review and authority | PASS — documented tests/audits |
| P3-A04 | Entity owns lifecycle and persistence | Cognition composed at daemon root and injected into `EntitySupervisor` | PASS — code-inspected |
| P3-A05 | Gating, freshness, dedupe and metadata-only observation | P3-3 integration audit and runtime tests | PASS — documented/test evidence |
| P3-A06 | Journal, replay and receipt behavior | P3-3/P3-4 reliability evidence | PASS — documented/test evidence |
| P3-A07 | Authority boundary | Runtime-owned result publication and Brain review; external forged result rejected in runtime test | PASS — test evidence |
| P3-A08 | Cancellation and cleanup | C1/C3 replay behavior, startup cleanup and shutdown tests | PASS WITH RECORDED LIMITATIONS |
| P3-A09 | Failure/timeout contract | Provider failure classification, cancellation, malformed output and shutdown tests; see companion contract | PASS for tested cases; full matrix not exhaustively demonstrated here |
| P3-A10 | Windows live integration | llama.cpp health/models endpoints; live Conversation and Cognition; reported 6 requested / 6 completed / 6 reviewed | PASS — owner-supplied Windows runtime evidence |
| P3-A11 | Runtime configuration record | Gemma 4 Artemis, llama.cpp, endpoint, shared slot and model inheritance inspected in daemon | PASS — see companion configuration record |
| P3-A12 | Structured output | Typed result validation, evidence-reference validation and malformed-output tests | PASS at contract/unit-test level |
| P3-A13 | Vietnamese quality measured | No controlled Vietnamese quality evaluation dataset/report supplied in the evidence reviewed | PARTIAL / NOT VERIFIED |
| P3-A14 | Resource and latency limits recorded | Shared semaphore (one inference slot), timeout contracts, server context 16896 and one observed Cognition completion around 10.968 s; no complete resource/latency profile supplied | PARTIAL |
| P3-A15 | Comparative benchmark | Owner directed that model/provider comparison and further model experimentation stop | OWNER-APPROVED SCOPE EXCLUSION; not a benchmark PASS |
| P3-A16 | Closure report and Owner acceptance | This evidence record is prepared; explicit final acceptance remains outstanding | PENDING |

## 5. Windows regression evidence

The Project Owner supplied the following direct Windows command outputs for the rebuilt source:

| Check | Result |
|---|---:|
| Python | 3.13.17 |
| pytest | 9.2.0 |
| Cognition tests | 42 passed |
| Entity Runtime tests | 121 passed |
| Full regression | **438 passed, 2 skipped** |
| Compile check | `python -m compileall -q src/sylphy_entity tests` passed |

These results were executed in the local rebuild directory on Windows and supplied by the Project Owner in this review. They are not inferred from the GitHub baseline and are not a new run performed by this document's author.

Historical results are kept distinct:
- P3-3 audit snapshot: 430 passed, 2 skipped.
- Later P3-3 completion report: 432 passed, 2 skipped.
- P3-4 reliability audit: 438 passed, 2 skipped; Entity Runtime suite 121 passed.

The current Windows full-suite result matches the P3-4 reported count, but the local rebuild has no Git HEAD, so no commit SHA can be attached to that run.

## 6. Reliability and lifecycle findings

Recorded P3-4 reliability evidence:
- Full suite: 438 passed, 2 skipped.
- Entity Runtime: 121 passed.
- No confirmed defects in the audited P3-4 scope.
- C1 (cancellation before coroutine begins): durable request is repaired by restart replay; no provider call.
- C3 (cancellation during result publication): durable result is retained and replay restores the receipt.
- Tests cover unexpected provider exception, resource busy, shutdown provider-close failure, and startup cleanup.

Additional daemon inspection:
- Conversation and Cognition use the same llama.cpp semaphore with capacity one.
- Cognition model defaults to the authoritative Conversation model.
- Cognition is gated by autonomy and explicit sandbox root.
- Supervisor shutdown is in a `finally` path.
- Cognition close is idempotent in `LlamaCppCognition`; a repeated close returns immediately.
- A failed underlying HTTP client close is not retried after `_closed` is set. The shutdown path records an unclean shutdown rather than claiming success.

The shutdown failure test verifies that a Cognition close exception is logged, clean-shutdown marking is withheld, lifecycle becomes `ERROR`, and terminal cleanup releases the lock/token and closes the database.

## 7. Owner-directed scope deviations

### SD-01 — Comparative model/provider benchmark excluded

The Project Owner fixed Gemma 4 Artemis and llama.cpp and directed that model/provider experimentation stop. Comparative benchmarking and model/provider selection are therefore excluded from this Phase 3 execution.

This is a recorded scope deviation from the original Master Plan deliverable for benchmark procedure, raw results and comparative report. It must not be described as a completed benchmark.

### SD-02 — Legacy Ollama compatibility retained (Owner-confirmed)

On 2026-11-28, the Project Owner explicitly confirmed the following Phase 3 decision:

> Keep the Ollama compatibility code, but do not use it in the default runtime.

This confirms the intended compatibility posture. The default Conversation and Cognition providers remain `llama.cpp`; Ollama remains available only as an explicit legacy selection. Retaining the adapter and CLI compatibility path is not permission to resume provider experimentation and does not alter the fixed Gemma 4 Artemis / llama.cpp runtime decision.

**Handling plan:**
1. Preserve `OllamaCognition` and `OllamaConversationGenerator` as legacy compatibility code; do not delete or rewrite them in Phase 3.
2. Keep `llama.cpp` as the default for both `--conversation-provider` and `--cognition-provider`.
3. Keep the legacy Ollama selection explicit and opt-in; do not select it automatically through fallback or provider discovery.
4. Do not add Ollama runtime tests, benchmarks, model downloads, or provider comparisons as new Phase 3 work. Existing compatibility code may remain covered by its pre-existing tests.
5. Verify the defaults in the daemon CLI and record them in the runtime configuration. No code change is required if the current source continues to satisfy these conditions.
6. Treat any future removal of Ollama compatibility as a separate Owner-approved change, not as part of this Phase 3 closure.

### SD-03 — ZIP rebuild has no Git history

The local rebuild was extracted from a ZIP. Its lack of HEAD/remote/reflog is expected for that delivery method. Targeted blob matches and reviewed source changes provide partial baseline traceability, not whole-tree provenance.

## 8. Remaining limitations and non-waived evidence gaps

1. No controlled Vietnamese quality measurement report was provided.
2. No complete RAM/VRAM/CPU/latency profile under a documented workload was provided. A single observed completion time is not a performance benchmark.
3. The ZIP archive's exact SHA-256 was not re-verified in this closure review.
4. Targeted file hashes do not prove the entire rebuild tree matches the baseline plus only intended Phase 3 changes.
5. The full P3-5 failure/timeout category matrix has not been shown as one exhaustive acceptance run; the contract and focused tests cover the cases identified in the supporting reports.
6. Explicit Project Owner final acceptance is pending.

These gaps are not silently waived by SD-01.

## 9. Final disposition

**Implementation and Windows regression: PASS within the inspected scope.**

**Phase 3 overall: CLOSURE CANDIDATE — PASS WITH OWNER-APPROVED SCOPE DEVIATION AND RECORDED LIMITATIONS; FINAL OWNER ACCEPTANCE PENDING.**

Do not mark Phase 3 unconditionally closed until the Project Owner explicitly accepts this record and the remaining non-waived evidence limitations. This record does not authorize Phase 4.

---

## 10. Source records

- `SYLPHY_MASTER_PLAN_FUTURE_HANDOVER_v3.0.md`, Section 9 — Phase 3 scope, deliverables and exit gate.
- `SYLPHY — MASTER HANDOVER & PHASE 3 ROADMAP`, Version 2.0, 2026-11-26 — Sections 7.1–7.8, including P3-5 Failure/Timeout Contract and P3-6 Acceptance.
- GitHub Phase 2 closure: `docs/rebuild/PHASE_2_CLOSURE_RECORD.md` at baseline merge commit `a33a829690246a3448cddd85c7dc68854e144348`.
- P3-3, P3-4 and Windows acceptance reports supplied during the project review.
- Project Owner-supplied Windows test output and daemon/lifecycle source excerpts reviewed on 2026-11-27.
