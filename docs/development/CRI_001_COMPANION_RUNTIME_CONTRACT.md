# CRI-001 — Companion Runtime Contract

**Status:** Contract primitives implemented; full runtime loop intentionally not implemented.  
**Code:** `src/sylphy/companion_contract.py`  
**Tests:** `tests/test_companion_contract.py`  
**Authority:** This contract does not replace `Runtime`, `identity.toml`, `InteractionPolicy`, or SQLite persistence.

## 1. Purpose

Define the smallest typed contract that future Companion Runtime integration must obey:

```
EVENT
  -> CONTEXT
  -> DECISION
  -> POLICY
  -> ACTION
  -> OUTCOME
  -> STATE UPDATE
```

This is a contract, not permission to build the whole loop. CRI-0 provides validated records and deterministic boundaries. It starts no worker, schedules no work, and executes no autonomous action.

## 2. Design rules

1. Reuse existing types where they already exist. `InteractionAction`, `InteractionPolicy`, `PolicyConditions`, and `PolicyDecision` are reused rather than duplicated.
2. No new dependency.
3. No new event bus, scheduler, worker, database, identity engine, emotion engine, or avatar framework.
4. Confirmed evidence must remain distinguishable from inference and missing information.
5. A denied decision must not produce an action.
6. A proposed action must not be recorded as successful.
7. State updates must be explicit and evidence-based.
8. No state field may claim continuous awareness, offline activity, or experience.

## 3. Type map

| Contract type | Purpose | Existing reuse |
|---|---|---|
| `CompanionEvent` | Validated event entering the companion system | New; source values align with `InteractionPolicy` sources |
| `CompanionContext` | Evidence available to a decision | New; no memory retrieval is performed by the type |
| `ContextItem` | One evidence item with an explicit kind | New; mirrors confirmed/current/inferred/missing distinction |
| `Decision` | Proposed response to an event | New; uses existing `InteractionAction` |
| `CompanionPolicy` | Deterministic policy evaluation | Delegates to existing `InteractionPolicy.decide()` |
| `PolicyEvaluation` | Policy result with allowed/rejected and effective action | Wraps existing `PolicyDecision` |
| `Action` | Permitted, contract-level operation | New; only executable actions are representable |
| `Outcome` | Truthful record of what happened | New; statuses distinguish proposal from success |
| `CompanionState` | Minimal evidence-based runtime state | New; does not replace `RuntimeState` |

## 4. Event

`CompanionEvent` contains only routing and interpretation data:

- `event_id` — opaque, validated identifier.
- `event_type` — finite `CompanionEventType`.
- `occurred_at` — timezone-aware timestamp.
- `source` — finite `CompanionEventSource` (`conversation`, `exploration`, `presence`, `investigation`).
- `session_id` — optional opaque session identifier.
- `correlation_id` — optional opaque correlation identifier.
- `payload` — immutable string-to-string mapping for bounded routing data.
- `expires_at` — optional timezone-aware expiry.

Validation rejects empty identifiers, unknown event types/sources, naive timestamps, non-string payloads, empty payload values, unsafe payload keys, and oversized values. `to_dict()` and `to_json(sort_keys=True)` provide stable serialization.

## 5. Context

`ContextItem.kind` is one of:

| Kind | Meaning | Required evidence |
|---|---|---|
| `CONFIRMED` | Stored, confirmed information | `source_id` required |
| `CURRENT` | Current interaction information | No inference confidence |
| `INFERRED` | Inference or interpretation | `confidence` required (0–1) |
| `MISSING` | Information that is not available | No source or confidence |

`CompanionContext` exposes `confirmed`, `current`, `inferred`, and `missing` properties. An inference is never returned by `confirmed`. Missing information is represented as missing, not as an empty confirmed value.

The contract type does **not** retrieve memory. Retrieval remains in `ContextBuilder` and `MemoryService`.

## 6. Decision

`Decision` is a proposed response:

- `event_id` — must match the event.
- `action` — existing `InteractionAction`.
- `rationale` — bounded explanation.
- `decision_id` — opaque identifier.
- `message` — required only for `SPEAK`.
- `permission_scope` — required only for `REQUEST_PERMISSION`.
- `not_before` — optional only for `WAIT`.

Supported actions are the existing `InteractionAction` values: `SPEAK`, `EXPLORE`, `WAIT`, `REQUEST_PERMISSION`, `SILENCE`. `EXPLORE` is retained because it already exists in `InteractionPolicy` and the Exploration subsystem; it is not permission to start autonomous research. No `START_TASK` or scheduling action is introduced.

## 7. Policy

`CompanionPolicy.evaluate(event, decision, conditions)` delegates to the existing `InteractionPolicy.decide()`.

It:

1. Rejects a decision whose `event_id` does not match the event.
2. Converts the event and decision into the existing `InteractionEvent` contract.
3. Overrides `PolicyConditions.requested_action` with the proposed decision action.
4. Returns a `PolicyEvaluation` with:
   - `allowed` — whether policy did not silence a non-silence proposal;
   - `effective_action` — the action policy actually selected;
   - `reason_code` — the existing `InteractionReason`;
   - `policy_decision` — the underlying `PolicyDecision`.

A denied evaluation resolves to `SILENCE`. A policy-modified `WAIT` may be allowed, but it is not executable as an action. Permission, expiry, duplicate, cooldown, queue, resource, grounding, and relevance gates remain owned by `InteractionPolicy`; they are not reimplemented.

## 8. Action

`Action` is a permitted, contract-level operation. It is created only from an allowed `PolicyEvaluation`:

```python
action = Action.from_evaluation(evaluation)
```

Creation is rejected when:

- the evaluation is denied;
- the effective action is `SILENCE` or `WAIT`;
- the action is not `SPEAK`, `EXPLORE`, or `REQUEST_PERMISSION`.

`Action` records identifiers, the effective action, immutable parameters, and a timestamp. It does not execute anything.

## 9. Outcome

`OutcomeStatus` distinguishes:

| Status | Meaning |
|---|---|
| `PROPOSED` | A decision exists; nothing has been allowed or executed. |
| `ALLOWED` | Policy allowed the decision; no execution is claimed. |
| `EXECUTED` | An action was attempted; success is not claimed. |
| `SUCCEEDED` | The action succeeded; non-empty execution evidence is required. |
| `FAILED` | The action failed; non-empty failure evidence is required. |
| `CANCELLED` | The action was cancelled. |
| `REJECTED` | Policy rejected the decision. |

`Outcome.proposed()` is never successful. `Outcome.succeeded()` and `Outcome.failed()` require evidence. `PROPOSED`, `ALLOWED`, and `REJECTED` cannot carry an `action_id`; `EXECUTED`, `SUCCEEDED`, `FAILED`, and `CANCELLED` require one.

## 10. State

`CompanionState` is deliberately minimal:

- `revision` — monotonic explicit update counter.
- `last_event_id` — last observed event.
- `last_outcome_id` — last recorded outcome.
- `last_observed_at` — timezone-aware observation time.
- `conversation_active` — whether a conversation generation is currently active.
- `pending_presence_notices` — count of pending Presence notices.

It has no field for awareness, consciousness, emotion, waiting, offline activity, or continuous experience. `updated()`:

- requires a matching event/outcome;
- rejects `PROPOSED` outcomes;
- rejects backward-moving observation timestamps;
- increments `revision` by exactly one;
- records only explicitly supplied state values.

## 11. Invariants

| ID | Invariant | Test |
|---|---|---|
| CRI-I1 | Confirmed evidence is distinguishable from inference and missing information. | `test_context_distinguishes_confirmed_inferred_and_missing` |
| CRI-I2 | Missing information is never represented as confirmed. | `test_context_rejects_inference_without_confidence_and_confirmed_without_source` |
| CRI-I3 | A denied decision cannot produce an action. | `test_action_requires_allowed_evaluation` |
| CRI-I4 | A policy-modified wait is allowed but not executable. | `test_policy_modified_wait_is_allowed_but_not_actionable` |
| CRI-I5 | A proposed action is not automatically successful. | `test_outcome_proposed_is_not_success` |
| CRI-I6 | Success and failure require evidence; statuses remain distinct. | `test_outcome_status_constraints` |
| CRI-I7 | State updates are explicit and monotonic. | `test_state_update_is_explicit_and_monotonic` |
| CRI-I8 | State has no unsupported awareness/activity claim. | `test_state_starts_without_claims` |
| CRI-I9 | No autonomous task action is introduced. | `test_contract_does_not_define_autonomous_actions` |

## 12. Non-goals

This contract does not implement:

- an event bus or dispatcher;
- a scheduler or background worker;
- autonomous task execution;
- a new memory store or memory confirmation path;
- a new identity engine;
- emotion inference or expression;
- avatar/VRM behavior;
- a replacement for `Runtime`.

The first intended integration slice is specified in `CRI_002_VERTICAL_SLICE_CONTRACT.md`.
