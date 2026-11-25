# CRI-005 — Bounded Autonomy

**Status:** Implemented and tested in Turn 7.  
**Code:** `src/sylphy/companion_contract.py` (`AutonomyDecision`), `src/sylphy/companion_slice.py` (`propose_autonomy_decision`, `record_autonomy_execution`, `AutonomyResult`), `src/sylphy/runtime.py` (`_maybe_autonomously_execute`, `_execute_autonomy_action`, `set_autonomy_enabled`, `autonomy_execution_enabled`), `src/sylphy/config.py` (`autonomy.enabled`).  
**Tests:** `tests/test_companion_autonomy.py` (AU-01 to AU-16).  
**Authority:** This document describes only implemented behavior. It does not extend the CRI-001 contract beyond the autonomy decision, replace `Runtime`, `InteractionPolicy`, `InvestigationExecutor`, or SQLite persistence, and does not describe future capabilities as implemented.

## 1. Verdicts

| Stage | Status |
|---|---|
| Capability implemented | **IMPLEMENTED** |
| Runtime integration implemented | **IMPLEMENTED** |
| Behavior tested | **TESTED** |
| Verified against real event source | **TESTED** (via the delivered Presence boundary) |
| Production wiring validated | **INTEGRATED** (config flag + runtime attribute + app wiring) |
| Live autonomous execution verified | **NOT VERIFIED** (requires a live local model + search provider; tests use fakes) |

## 2. Audited foundation (Turn 6 reuse)

Before implementing, the repository was audited and found suitable existing capabilities:

| Requirement | Finding |
|---|---|
| Companion runtime / event loop | Turn 6 pipeline (`PRESENCE_NOTICE_READY` -> context -> decision -> policy -> outcome -> state) in place. |
| Decision + interaction policy | Deterministic; no LLM; actions `SPEAK` / `EXPLORE` / `WAIT` / `REQUEST_PERMISSION` / `SILENCE`; permission conditions (`permission_granted`, `permission_denied`, `request_permission`) already enforced. |
| Existing task execution / cancellation | `InvestigationExecutor` (bounded search/read/analyze pass), `Runtime.start_investigation`, `Runtime.cancel_investigation`, timeout and preemption handling. |
| Persistence | `companion_outcomes` (`UNIQUE event_id`), `companion_state`, atomic outcome+state commit. |
| Failure / recovery | Executor terminal statuses, runtime failure isolation. |

**Conclusion:** the first autonomous action reuses the existing bounded Investigation capability rather than creating a new task system.

## 3. Implemented capability

SYLPHY can now, under strictly bounded conditions, start **one** bounded Investigation for a real, delivered Presence event.

### 3.1 Trigger event

A saved Exploration question that reached the existing Presence-notice delivery boundary and was delivered (`Runtime._publish_exploration_question` -> presence `SPEAK` permitted).

### 3.2 Action performed

Start one bounded Investigation pass for that saved question through the existing `InvestigationExecutor`.

### 3.3 Execution pathway

```
Delivered Presence event (real event)
    -> AutonomyDecision (deterministic; event + context + conditions)
    -> CompanionPolicy -> existing InteractionPolicy
    -> permission gate (PolicyConditions)
    -> bounded scope check (decision scope vs persisted investigation usage)
    -> Bounded Execution (InvestigationExecutor / start_investigation)
    -> Outcome (SUCCEEDED / FAILED / CANCELLED / ALLOWED / silenced)
    -> Durable companion outcome + bounded companion state update
```

### 3.4 Decision content

`AutonomyDecision` contains: decision id, trigger event id, proposed action, action category, bounded scope, required permissions, evidence source ids, reason, and expiry.

### 3.5 Permission model

- No permission -> no execution (policy resolves `REQUEST_PERMISSION` / silence).
- Permission requested but not granted -> decision `REQUEST_PERMISSION`, outcome `ALLOWED`, no execution.
- Permission denied -> silenced, no execution.
- Execution additionally requires the permission gate through the existing policy conditions.

### 3.6 Scope boundaries

- One action per trigger; one investigation per question.
- Scope limits (duration, model calls, search requests, pages, source bytes, extracted chars) taken from the bounded investigation budget.
- Execution refuses when persisted investigation usage already meets/exceeds the proposed scope.

### 3.7 Feature flag

`[autonomy] enabled` (default **false**); runtime attribute `autonomy_enabled`; default OFF. Companion remains disabled by default.

## 4. Implemented vs not implemented

**IMPLEMENTED**
- Deterministic autonomy proposal for delivered presence events.
- Policy-driven permission gate and execution.
- Bounded execution via existing investigation executor.
- Truthful outcome + durable state commit.
- Failure isolation and duplicate handling.

**NOT IMPLEMENTED**
- Continuous autonomy, background autonomy worker, autonomous loops, proactive messaging, autonomous memory retrieval, autonomous task creation loops.
- Autonomous action beyond one bounded investigation pass per delivered question.

## 5. Tests

| Test | Verifies |
|---|---|
| AU-01 | Feature disabled -> no autonomous execution. |
| AU-02 | Valid real event triggers eligible bounded action (Runtime boundary). |
| AU-03 | Action executes successfully; SUCCEEDED outcome with action id recorded. |
| AU-04 | Permission denied -> no execution. |
| AU-05 | Permission requested without grant -> no execution, outcome ALLOWED. |
| AU-06 | Insufficient evidence -> no execution. |
| AU-07 | Duplicate trigger -> ignored once, single execution. |
| AU-08 | Duplicate action id -> deterministic decision/action reuse. |
| AU-09 | Action exceeding scope -> refused. |
| AU-10 | Execution timeout -> FAILED outcome. |
| AU-11 | Execution failure -> FAILED outcome. |
| AU-12 | Cancellation -> CANCELLED outcome. |
| AU-13 | Persistence failure -> isolated. |
| AU-14 | Runtime restart and recovery -> no duplicate autonomous run. |
| AU-15 | No recursive autonomous task creation. |
| AU-16 | Existing functionality unchanged when autonomy disabled. |

### Test results

| Verification | Result |
|---|---|
| New autonomy tests | **16 passed** (`AU-01`–`AU-16`) |
| Companion + autonomy + exploration + investigation + presence + policy + runtime regression set | **221 passed** |
| Full suite | **445 passed, 1 deselected** |
| Integration suite | **1 passed, 445 deselected** |

Full-suite baseline movement: 417 (Turn 6 end) + 16 new autonomy tests = **445 passed**.

## 6. What SYLPHY can now genuinely do

- When a saved Exploration question is delivered through the real Presence boundary, and autonomy is enabled, SYLPHY deterministically proposes and (if permitted and in scope) starts one bounded Investigation.

## 7. What SYLPHY still cannot do

- No continuous autonomy, no autonomous loop, no autonomous messaging, no autonomous memory access.
- No LLM judgment; deterministic policy only.
- No permission -> no execution; requests are not silently granted.
- Default OFF.

## 8. Limitations

- Live execution (real local model + search provider) is **not** verified in tests; only fake providers were used.
- The autonomous action is investigation-only; it does not create new autonomous tasks.
- Scope/duration enforcement relies on the existing executor; timeout handled by executor.

## 9. Repository state

- Branch `master`; HEAD unchanged from baseline.
- No commit created.
- Pre-existing modifications preserved.

---

*End of CRI-005.*
