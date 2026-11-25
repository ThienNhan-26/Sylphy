# CRI-002 — First Vertical Slice Contract

**Status:** Specification only. Not implemented in CRI-0/Turn 2.  
**Purpose:** Define the first integration slice precisely enough for Turn 3 implementation.  
**Depends on:** `src/sylphy/companion_contract.py`, `CRI_001_COMPANION_RUNTIME_CONTRACT.md`.

## 1. Slice statement

> A completed conversation turn produces one durable, truthful outcome record and one bounded companion-state update, without autonomous action, external monitoring, scheduling, memory confirmation, or user-facing notification.

```
Completed conversation turn
    -> CompanionEvent(CONVERSATION_TURN_COMPLETED)
    -> CompanionContext(current turn evidence only)
    -> Decision(SPEAK, representing the already-produced assistant reply)
    -> CompanionPolicy.evaluate(...)
    -> Action.from_evaluation(...)          # contract-level record only
    -> Outcome.succeeded(..., evidence=assistant_message_id)
    -> CompanionState.updated(...)
```

The `SPEAK` action records the assistant reply that already occurred. It does not invoke the model, send a message, or perform an autonomous action.

## 2. Trigger

The slice runs only after `ConversationService.respond()` has:

1. received a non-empty model response;
2. persisted the completed user/assistant turn atomically;
3. returned a `Turn` to `Runtime.run_conversation()`.

If the provider fails, the response is empty, or the turn is not persisted, the slice does not create a successful outcome.

## 3. Event construction

| Field | Value |
|---|---|
| `event_type` | `CONVERSATION_TURN_COMPLETED` |
| `source` | `CONVERSATION` |
| `session_id` | persisted session ID |
| `payload` | bounded identifiers only: `user_message_id`, `assistant_message_id`; no message text |
| `occurred_at` | completion time, timezone-aware |
| `correlation_id` | optional turn/session correlation ID |

No raw conversation text is placed in the event payload. The outcome evidence references the persisted assistant message ID.

## 4. Context construction

The slice uses only:

- one `ContextItem(kind=CURRENT, content="Completed conversation turn", source_id=assistant_message_id)`;
- optionally one `ContextItem(kind=CONFIRMED, ...)` only if the existing `ContextBuilder` already retrieved that exact confirmed memory for the turn; the slice must not add new retrieval.

The slice must not:

- promote a cognition candidate;
- create or update a memory;
- infer a new fact;
- add a missing-information claim that is not explicit.

## 5. Decision and policy

The decision is:

- `action=SPEAK`;
- `message` equal to the persisted assistant response, or a bounded reference if the response is too large for the contract;
- `rationale="Assistant response was persisted for the completed turn."`

Policy evaluation uses the existing `InteractionPolicy` with application-verified conditions. The slice must not hard-code `allowed=True` without calling policy.

If policy returns `SILENCE`, the outcome is `REJECTED`, no action is created, and the state update records the rejection. The conversation itself remains successful and unchanged.

## 6. Durable outcome

Turn 3 may add one table to the existing SQLite database, for example `companion_outcomes`, with at minimum:

- `outcome_id` primary key;
- `event_id`;
- `decision_id`;
- `action_id` nullable;
- `status`;
- `detail`;
- `evidence` nullable;
- `created_at`.

No new database file and no new memory table. The outcome write must be atomic and idempotent per completed turn. A duplicate trigger for the same `event_id` must not create a second successful outcome.

## 7. State update

The state update:

- uses `CompanionState.updated()`;
- increments `revision` by exactly one;
- records `last_event_id` and `last_outcome_id`;
- sets `conversation_active=False` after the turn;
- sets `pending_presence_notices` from the current Presence queue length;
- does not claim offline activity, waiting, awareness, or emotion.

State is in-memory for this slice unless a later decision explicitly adds durable state. If in-memory, the outcome remains durable and the state can be rebuilt from the latest outcome on startup.

## 8. Acceptance criteria

| ID | Criterion | Verification |
|---|---|---|
| VS-01 | A completed, persisted turn creates exactly one `CONVERSATION_TURN_COMPLETED` event. | Integration test with a fake provider and temporary SQLite database. |
| VS-02 | The event payload contains only bounded identifiers, not raw message text. | Assert payload keys and values. |
| VS-03 | The context contains current-turn evidence; no inference is labeled confirmed. | Assert `CompanionContext.confirmed` excludes inferred items. |
| VS-04 | Policy is actually called; `allowed=True` is not hard-coded. | Use a stub/fake policy and assert invocation, or assert a denied condition produces `REJECTED`. |
| VS-05 | A denied decision creates no `Action` and records `OutcomeStatus.REJECTED`. | Assert `action_id is None` and status. |
| VS-06 | A successful slice records `OutcomeStatus.SUCCEEDED` with non-empty evidence referencing the persisted assistant message ID. | Assert outcome row and evidence. |
| VS-07 | `OutcomeStatus.PROPOSED` is never persisted as success. | Assert proposed path is not used for a completed turn. |
| VS-08 | State revision increments by exactly one per completed slice. | Assert before/after revision. |
| VS-09 | State records no awareness, waiting, offline activity, or emotion. | Assert `CompanionState` fields and absence of such attributes. |
| VS-10 | A duplicate trigger for the same event does not create a second successful outcome. | Call the slice twice; assert one successful row. |
| VS-11 | Outcome persistence failure does not change conversation persistence or report success. | Inject a store failure; assert conversation remains persisted and no false `SUCCEEDED` row. |
| VS-12 | No cognition confirmation, memory write, exploration enqueue, presence delivery, or user-facing notification occurs. | Assert table counts and presence queue unchanged. |
| VS-13 | Existing conversation persistence semantics are unchanged. | Run existing conversation/foundation tests; inspect `git diff`. |
| VS-14 | The slice requires no live model and no network. | Test uses a fake provider and temporary database. |

## 9. Explicit non-goals for the slice

- No autonomous action.
- No external monitoring.
- No scheduler or background worker.
- No background activity claims.
- No new memory confirmation path.
- No automatic user-facing notification.
- No modification of conversation persistence semantics.
- No new identity authority.
- No emotion or avatar expression.

## 10. Turn 3 implementation order

1. Add the durable outcome table and store methods to the existing SQLite database.
2. Add a pure `record_completed_turn(...)` function or small service that builds the event, context, decision, policy evaluation, action, outcome, and state update.
3. Wire it once, after a completed turn is persisted, behind a configuration flag that defaults to off.
4. Add the VS-01–VS-14 tests.
5. Run the focused tests, the existing relevant tests, and the full suite.
6. Inspect `git diff` and report exact results.

Do not start CRI continuity, presence expansion, bounded autonomy, or expression work in Turn 3.
