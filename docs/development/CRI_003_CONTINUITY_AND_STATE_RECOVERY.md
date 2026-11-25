# CRI-003 — Continuity and State Recovery

**Status:** Implemented in Turn 5.  
**Code:** `src/sylphy/companion_slice.py` (`record_completed_turn`, `restore_companion_state`), `src/sylphy/storage.py` (`companion_outcomes`, `companion_state`), `src/sylphy/companion_contract.py` (`CompanionState`).  
**Tests:** `tests/test_companion_continuity.py` (CT-01 to CT-10), `tests/test_companion_slice.py`, `tests/test_companion_contract.py`.  
**Authority:** This document describes the implemented behavior. It does not extend the CRI-001 contract and does not replace `Runtime`, `identity.toml`, `InteractionPolicy`, or SQLite persistence.

## 1. Purpose

Make the existing Companion Runtime state semantically correct, recoverable, and testable across application restart. Continuity is grounded only in stored evidence: the runtime can reconstruct its durable Companion State after creating a new Runtime instance against the same database.

This is **not** a memory system. It does not add embeddings, a vector database, autonomous behavior, a scheduler, background workers, monitoring, or notifications.

## 2. Durable state

Durable state is the singleton row in the `companion_state` table (id = 1), written by the existing SQLite store inside a single `BEGIN IMMEDIATE` transaction together with one `companion_outcomes` row per completed turn. It survives process restart and is the source of durable truth.

| Field | Meaning | Evidence source |
|---|---|---|
| `revision` | Monotonic explicit update counter, incremented by exactly one per recorded non-duplicate outcome. | Count of recorded companion state updates, each tied to a recorded outcome. |
| `last_event_id` | Last observed (processed) event identifier. | The event that produced the last recorded outcome. |
| `last_outcome_id` | Last recorded outcome identifier. | The outcome row written for the last event. |
| `last_observed_at` | Timezone-aware observation time of the last recorded event. | The actual `occurred_at` of the recorded event; never "now" and never fabricated. |
| `conversation_active` | Historical snapshot: whether a conversation generation was active at the last completed turn. | Always written `False` by the slice after a completed turn; never `True` in durable state. |
| `pending_presence_notices` | Historical snapshot: count of pending Presence notices taken at the last completed turn. | The Presence queue length observed when the turn completed. |

No field asserts awareness, consciousness, emotion, waiting, offline activity, or continuous experience. `CompanionState` has no such attributes.

## 3. Transient state

Transient state exists only during the current process and is **not** restored across restart:

- The in-process `CompanionState` object held by a slice call (rebuilt from durable state at each boundary).
- `RuntimeState` (phase, `generation_active`, `active_operation`, `last_activity`, uptime, last error) — this is operational runtime telemetry, not Companion State, and is not persisted.
- The in-memory Presence queue projection (rebuilt from durable queued exploration notices via `restore_pending_presence`, which is unrelated to companion state).

Nothing in Companion State is intentionally ephemeral: every `CompanionState` field is durable and restored from the database. Fresh in-process state (a `CompanionState()` with no durable row) resets to defaults: `revision=0`, no `last_event_id`/`last_outcome_id`/`last_observed_at`, `conversation_active=False`, `pending_presence_notices=0`.

## 4. State lifecycle

The deterministic lifecycle boundary is the completion of a conversation turn, handled by `record_completed_turn`:

1. The turn is already durably persisted by `ConversationService` before the slice runs.
2. The slice builds the CRI event/context/decision, evaluates `InteractionPolicy` through `CompanionPolicy`, and produces a truthful `Outcome`.
3. **Durable state is restored first:** `restore_companion_state(store)` reads the singleton `companion_state` row from the database (or returns fresh initial state when none exists). State is rebuilt from the database, never carried over from a previous `Runtime` instance.
4. The restored state is the base for `CompanionState.updated(...)`, which increments `revision` by exactly one, records `last_event_id`/`last_outcome_id`/`last_observed_at`, sets `conversation_active=False`, and records `pending_presence_notices`.
5. Outcome insert and state update run in one SQLite transaction. The `UNIQUE` constraint on `companion_outcomes.event_id` enforces idempotency: re-processing the same event inserts nothing and applies no further state revision.
6. The slice writes no message text, performs no memory retrieval, no LLM call, no presence announcement, and no user notification.

Restoration never triggers LLM calls, memory retrieval, Presence announcements, user notifications, exploration, or autonomous actions.

## 5. Startup restoration

A newly created `Runtime` against an existing database recovers durable state through `record_completed_turn` (and the public `restore_companion_state(store)` helper) at the turn boundary. There is no separate startup restoration hook that runs work on boot: companion state is only read when a turn is actually recorded. This means:

- No `Runtime` restart changes `revision` (no revision increments caused solely by application startup).
- A new `Runtime` instance does not rely on an in-memory object from a previous instance.
- No companion behavior is activated merely by starting the runtime when the feature is disabled.

## 6. Event / outcome relationship

One `companion_outcomes` row is written per completed, persisted turn. The row stores `outcome_id`, `event_id`, `decision_id`, `action_id` (nullable), `status`, `detail`, `evidence` (nullable), and `created_at`.

- `event_id` is derived deterministically from `session_id` + assistant message id (SHA-256), so re-processing the same turn yields the same `event_id`.
- `OutcomeStatus.PROPOSED` is never persisted as success; `SUCCEEDED`/`FAILED` require execution evidence.
- `companion_state.last_outcome_id` references an outcome row that exists; state never references a nonexistent outcome.
- Historical outcomes remain accessible via `list_companion_outcomes` (newest first) and `get_companion_outcome(event_id)`.

## 7. Idempotency

Repeated processing of the same event:

- Does not duplicate the outcome (the `UNIQUE` constraint on `event_id` causes the second insert to be a no-op).
- Does not increment `revision` more than once (no state change is applied on a duplicate).
- Does not corrupt restored state (the duplicate branch returns the current stored state row).

This holds across process restarts because `event_id` is deterministic and the constraint is in the database.

## 8. Failure handling

- **Bookkeeping failure:** `Runtime.run_conversation` wraps `record_completed_turn` in `try/except`. A bookkeeping failure is logged and does not fail the conversation; the already-persisted turn remains valid and no companion outcome/state row is left behind (atomic rollback in `record_companion_outcome`).
- **Transaction failure:** `record_companion_outcome` runs outcome insert and state update in one `BEGIN IMMEDIATE` transaction; a partial write rolls back completely.
- **Restart after failure:** restarting against the same database does not invent a missing outcome. The restored state reflects only outcomes that were durably committed.

## 9. Truthfulness boundaries

Restored state cannot be interpreted as:

- **Continuous awareness:** `last_observed_at` describes an actual recorded past event, not current awareness. A restored timestamp is in the past relative to process "now."
- **Continuous monitoring:** `pending_presence_notices` is a historical snapshot taken at turn completion, not proof the system is currently monitoring the user.
- **Human-like memory:** no inference is promoted to confirmed state; state only records observed events and outcomes.
- **Activity during downtime:** restart does not increment `revision`, does not set `conversation_active=True`, and does not fabricate events or outcomes.
- **Unobserved user behavior:** state records only events the system actually processed.

`conversation_active` is never `True` in durable state because the slice always writes `False` after a completed turn, satisfying the requirement not to persist an assertion of ongoing activity without reliable evidence.

## 10. Tests and evidence

`tests/test_companion_continuity.py` (CT-01 to CT-10), using temporary SQLite databases, a fake provider, no Ollama, and no network:

| Test | Verifies |
|---|---|
| CT-01 | Fresh database produces valid initial state: `revision=0`, no fabricated historical event, no active conversation, no pending activity. |
| CT-02 | Completed turn persists state; durable state and durable outcome agree (`last_outcome_id` == outcome id, `revision=1`). |
| CT-03 | Runtime restart: Runtime A records a turn, Runtime B against the same database recovers durable state; revision continues (not reset) and no in-memory state is carried over. |
| CT-04 | Multiple turns: revision progression, latest event/outcome id, historical outcomes remain accessible. |
| CT-05 | Duplicate event: one durable outcome, no duplicate revision increment, consistent state restored across a fresh store handle. |
| CT-06 | Interrupted bookkeeping: conversation remains valid, no false success, restart does not invent a missing outcome. |
| CT-07 | Ephemeral state: `conversation_active` and `pending_presence_notices` are historical snapshots that reset; fresh in-process state defaults to inactive. |
| CT-08 | Truthfulness: restored `last_observed_at` describes a real recorded event; no awareness/emotion/waiting/offline fields; no ongoing monitoring claim. |
| CT-09 | Feature disabled: normal conversation works, no companion state is modified, restoration does not activate companion behavior. |
| CT-10 | Database integrity: atomic rollback leaves no partial state; state and outcome rows stay consistent (`revision ==` outcome count, `last_event_id`/`last_outcome_id` match the latest outcome). |

The pre-existing CRI-001 tests (`tests/test_companion_contract.py`) and vertical-slice tests (`tests/test_companion_slice.py`) continue to pass.

## 11. Explicit limitations

- Companion state records completed-turn bookkeeping only; it is not a general memory, episodic, or semantic memory system.
- No inference, cognition candidate, or memory confirmation is written to companion state.
- `pending_presence_notices` and `conversation_active` are snapshots at turn completion, not live signals of current activity.
- Restoration occurs at the turn-completion boundary, not as an autonomous startup process.
- The companion feature remains disabled by default (`companion.enabled = false`); enabling it is opt-in via configuration.
- No LLM call, network access, memory retrieval, presence announcement, user notification, exploration, or autonomous action is triggered by persistence, restoration, or startup.
