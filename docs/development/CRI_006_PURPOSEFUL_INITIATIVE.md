# CRI-006 — Purposeful Initiative

**Status:** Implemented and tested in Turn 8.
**Code:** `src/sylphy/companion_contract.py` (`InitiativeAction`, `InitiativeDecision`), `src/sylphy/companion_slice.py` (`initiative_trigger_event`, `build_initiative_context`, `propose_initiative_decision`, `record_initiative`, `InitiativeResult`), `src/sylphy/runtime.py` (`_maybe_initiate_investigation`, `set_initiative_enabled`, `initiative_execution_enabled`), `src/sylphy/config.py` (`initiative.enabled`), `src/sylphy/app.py` (wiring).
**Tests:** `tests/test_companion_initiative.py` (IN-01 to IN-16).
**Authority:** This document describes only implemented behavior. It does not add a general initiative system, scheduler, background worker, or autonomous loop, and it does not describe future capabilities as completed.

## 1. Verdicts

| Stage | Status |
|---|---|
| Capability implemented | **IMPLEMENTED** |
| Runtime integration implemented | **IMPLEMENTED** |
| Behavior tested | **TESTED** |
| Verified against real event source | **TESTED** (via delivered Presence event -> bounded autonomy -> investigation completion) |
| Production wiring validated | **INTEGRATED** (config flag + runtime attribute + app wiring) |
| Live initiative delivery verified | **NOT VERIFIED** (tests use fakes; no live model/network) |

## 2. Audited foundation (Turn 6/7 reuse)

Before implementing, the existing system was audited and found suitable reusable components:

| Requirement | Finding |
|---|---|
| Turn 6 Presence pipeline | `PRESENCE_NOTICE_READY` -> context -> decision -> policy -> outcome -> state, with delivery callback only at foreground boundaries. |
| Turn 7 autonomy | Deterministic `AutonomyDecision` -> policy -> bounded investigation execution -> truthful outcome. The first autonomous action was bounded investigation start. |
| Investigation execution/completion | `InvestigationExecutor` runs one bounded pass; completion persists a `InvestigationResult` with `evidence_sufficient`; terminal statuses exist. |
| Decision/policy | Deterministic `InteractionPolicy`; actions `SPEAK`/`EXPLORE`/`WAIT`/`REQUEST_PERMISSION`/`SILENCE`; permission conditions (`permission_granted`, `permission_denied`, `request_permission`). |
| Delivery mechanism | Existing `PresenceService` queue with deduplication by `(source_id, deduplication_key)`; delivery only at observed foreground boundaries. |
| Persistence/idempotency | `companion_outcomes` UNIQUE on `event_id`; state singleton; atomic outcome+state commit. |

**Conclusion:** the first meaningful initiative reuses the existing Companion Runtime, Interaction Policy, Presence delivery, and Investigation persistence. No parallel system was created.

## 3. Implemented capability

SYLPHY may now evaluate whether one completed bounded Investigation warrants a user-facing initiative response, and — under deterministic policy — deliver at most one bounded response.

### 3.1 Trigger event

A real, durably completed bounded investigation. In the integrated path this is the investigation produced by the Turn 7 autonomous action after it reached `COMPLETED`.

Typed event: `CompanionEvent(INVESTIGATION_COMPLETED, source=INVESTIGATION, correlation_id=<investigation id>)`; event id is a deterministic hash of the investigation id.

### 3.2 Initiative decision

`InitiativeDecision` carries: decision id, trigger event id, source, proposed action (`COMMUNICATE`/`WAIT`/`SILENCE`/`REQUEST_PERMISSION`/`REJECT`), bounded action category, evidence source ids with classifications, proposed response, required permissions, reason, and validity boundary. No LLM participates.

### 3.3 Decision conditions (deterministic)

- Feature disabled -> propose `SILENCE`.
- Duplicate trigger -> propose `SILENCE`.
- Result missing / `evidence_sufficient` false -> propose `SILENCE` (no initiative from an incomplete or unsupported result).
- Delivery permission denied -> propose `REJECT` (policy resolves the communication proposal to a refusal).
- Delivery permission absent / `request_permission` -> propose `REQUEST_PERMISSION`.
- Cooldown with `can_wait` -> propose `WAIT`; without wait -> propose `SILENCE`.
- Delivery queue full -> propose `SILENCE`.
- Otherwise -> propose `COMMUNICATE`.

### 3.4 Execution pathway

```
Investigation COMPLETED + persisted result (real event)
    -> initiative_trigger_event / build_initiative_context
    -> propose_initiative_decision (deterministic)
    -> CompanionPolicy -> existing InteractionPolicy
    -> permission gate (PolicyConditions / InitiativeDecision.required_permissions)
    -> bounded delivery (PresenceService publish with initiative dedup key)
    -> Outcome (SUCCEEDED / FAILED / ALLOWED / REJECTED)
    -> durable companion outcome + bounded state update
```

### 3.5 Communication behavior

- `COMMUNICATE` + policy allows -> one bounded response is delivered through the existing Presence mechanism (pending queue, deduplicated by initiative key); outcome `SUCCEEDED` or `FAILED` truthfully.
- `WAIT` -> outcome allowed/deferred; no delivery, no retry loop.
- `SILENCE` -> outcome allowed/silenced; no delivery.
- `REQUEST_PERMISSION` -> outcome allowed; no delivery performed (a request is never a grant).
- `REJECT` -> policy denies -> outcome `REJECTED`; no delivery.

## 4. Implemented vs not implemented

**IMPLEMENTED**
- Deterministic initiative proposal for completed investigations.
- Policy-driven permission gate and delivery decision.
- Bounded delivery via the existing Presence mechanism.
- Truthful outcome and durable state commit.
- Failure isolation, duplicate handling, WAIT/SILENCE/REJECT semantics.

**NOT IMPLEMENTED**
- Continuous initiative, background initiative worker, initiative loops.
- Multiple initiative triggers or initiative types beyond one bounded response per completed investigation.
- Autonomous long-term goals or task chains.

## 5. Tests

| Test | Verifies |
|---|---|
| IN-01 | Feature disabled -> propose silence; no delivery. |
| IN-02 | Complete, relevant result -> `COMMUNICATE`; permitted delivery -> `SUCCEEDED`. |
| IN-03 | Incomplete result -> `SILENCE`; no delivery. |
| IN-04 | Insufficient evidence -> `SILENCE`; no delivery. |
| IN-05 | Duplicate trigger -> ignored once; no second delivery. |
| IN-06 | Duplicate communication -> not redelivered (dedup key). |
| IN-07 | Policy denies delivery -> `REJECTED`; no delivery. |
| IN-08 | Permission required -> `REQUEST_PERMISSION`; no delivery. |
| IN-09 | Permission denied -> `REJECT`; no delivery. |
| IN-10 | `WAIT` -> deferred; no retry. |
| IN-11 | `SILENCE` -> no delivery. |
| IN-12 | Failed investigation -> no initiative delivery. |
| IN-13 | Persistence failure -> isolated. |
| IN-14 | Runtime restart -> no duplicate initiative delivery. |
| IN-15 | Companion disabled -> no delivery, no persistence of action. |
| IN-16 | Actual Runtime integration: delivered Presence event -> autonomy -> investigation completion -> bounded initiative delivery. |

### Test results

| Verification | Result |
|---|---|
| New initiative tests | **16 passed** (IN-01 to IN-16) |
| Companion + autonomy + initiative + exploration + investigation + presence + policy + runtime regression set | **237 passed** |
| Full suite | **461 passed, 1 deselected** |
| Integration suite | **1 passed, 461 deselected** |

Full-suite movement: 445 (Turn 7 end) + 16 new Turn 8 tests = **461 passed**.

## 6. Integrated trigger path

Real Event -> Presence delivery (Turn 6) -> Autonomy action start (Turn 7) -> bounded investigation completion -> initiative evaluation (Turn 8) -> policy -> permission -> delivery -> outcome -> state.

## 7. What SYLPHY can now genuinely do

- After a bounded autonomous investigation completes with sufficient evidence, SYLPHY deterministically proposes one bounded initiative response and, when permitted, delivers it through the existing Presence mechanism.

## 8. What SYLPHY still cannot do

- No continuous initiative, no autonomous loop, no autonomous messaging beyond one bounded response per completed investigation, no autonomous memory access.
- No LLM judgment; deterministic policy only.
- No permission -> no delivery; requests are not silently granted.
- Default OFF (`[initiative] enabled` default false).

## 9. Limitations

- Live delivery (real local model + user-facing presence consumer) is **not** verified; fakes only.
- The response content is derived from the bounded investigation result summary (capped).
- Delivery persistence semantics reuse the Presence queue/dedup design; the user-facing frontier owns final delivery confirmation.

## 10. Repository state

- Branch `master`; HEAD unchanged from baseline.
- No commit created.
- Pre-existing modifications preserved.

---

*End of CRI-006.*
