# CRI-004 — Presence and Event-Driven Companion Loop

**Status:** Implemented and tested in Turn 6.  
**Code:** `src/sylphy/companion_slice.py` (`presence_notice_event`, `build_presence_context`, `propose_presence_decision`, `record_presence_event`, `PresenceResult`), `src/sylphy/runtime.py` (`_publish_exploration_question_with_companion`).  
**Tests:** `tests/test_companion_presence.py` (PR-01 to PR-12).  
**Authority:** This document describes only implemented behavior. It does not extend the CRI-001 contract, replace `Runtime`, `InteractionPolicy`, `PresenceService`, or SQLite persistence, and does not describe future capabilities as implemented.

## 1. Existing event sources

The repository can observe these real boundaries today:

| Event | Actual source | Observed evidence | Trigger boundary |
|---|---|---|---|
| Completed conversation turn | `ConversationService.respond()` returns after the turn is durably persisted | `Turn` with persisted user/assistant message IDs | `Runtime.run_conversation()` after persistence |
| Saved Exploration question ready for notice | `ExplorationWorker` persists a pending question, then calls `Runtime._publish_exploration_question()` through `on_question` | Durable `exploration_questions` row with `status="pending"` | The existing Exploration delivery callback |
| Explicit user command | CLI command router (`app.py`) | Parsed command text | CLI loop |
| Application lifecycle | `Runtime.start()` / `shutdown()` / `EventBus` | `RUNTIME_STARTED`, `RUNTIME_READY`, etc. | Runtime lifecycle |
| User message received | `Runtime.run_conversation()` publishes `USER_MESSAGE_RECEIVED` | `EventBus` event | Conversation start |

No monitoring, polling, user-return detection, webcam/microphone observation, scheduler, or fabricated event source exists or was added.

## 2. Implemented event source

Turn 6 uses one already-existing event source:

> **A saved Exploration question reaching the existing Presence-notice boundary** (`Runtime._publish_exploration_question`).

The question must already be durably stored and still eligible. The presence slice does not create questions, does not run Exploration inference, and does not inspect the user.

`IMPLEMENTED` — the event is a real, persisted application record.  
`TESTED` — PR-01, PR-02, PR-12.

## 3. Implemented pipeline

```
Saved Exploration question (durable event source)
    -> CompanionEvent(PRESENCE_NOTICE_READY, source=PRESENCE)
    -> CompanionContext(CONFIRMED notice exists, CURRENT delivery boundary,
                        CURRENT queue capacity, MISSING user availability)
    -> deterministic Decision proposal (SPEAK)
    -> existing CompanionPolicy -> existing InteractionPolicy
    -> if permitted: execute() queues and publishes one Presence notice
       if not permitted: SILENCE, no delivery
    -> truthful Outcome (SUCCEEDED / FAILED / REJECTED / ALLOWED)
    -> durable companion_outcomes row + bounded companion_state update
```

Code path:

1. `Runtime._publish_exploration_question()` validates the saved question, expiry, and ownership.
2. When Companion is enabled, it delegates to `_publish_exploration_question_with_companion()`.
3. `presence_notice_event()` builds the typed `CompanionEvent`. The event payload contains only `notice_id`; no question text and no user text enter the event payload.
4. `build_presence_context()` assembles explicit evidence items.
5. `propose_presence_decision()` proposes `SPEAK` deterministically. There is no LLM involvement.
6. `record_presence_event()` applies the existing `InteractionPolicy` through `CompanionPolicy`.
7. Only an allowed executable action may call the `execute` callback, which claims the exploration notification, publishes exactly one `PresenceEvent`, and sets the bounded `CURIOUS` presentation state.
8. The outcome and bounded state are written in the existing atomic SQLite transaction.

`IMPLEMENTED` — PR-02, PR-04, PR-05, PR-12.  
`NOT IMPLEMENTED` — no general event bus, no scheduler, no background monitor, no autonomous exploration trigger, no proactive messaging engine.

## 4. Evidence handling

`CompanionContext` distinguishes evidence explicitly:

| Item | Kind | Basis |
|---|---|---|
| Saved notice is pending delivery | `CONFIRMED` | The durable `exploration_questions` row, `source_id=notice_id` |
| Delivery occurs at an observed foreground boundary | `CURRENT` | The existing CLI prompt-boundary delivery rule |
| Presence queue currently has capacity / is full | `CURRENT` | The actual in-memory queue length at decision time |
| User availability while the notice is queued | `MISSING` | It is not observed and is not inferred |

The slice creates no `INFERRED` item, does not promote an inference to `CONFIRMED`, does not promote history to current activity, and does not promote missing information to observed information. No memory retrieval occurs.

`IMPLEMENTED` and `TESTED` — PR-03, PR-11.

## 5. Permission handling

Policy is the existing `InteractionPolicy`; the presence slice does not duplicate policy logic.

- `SPEAK` is permitted only when policy returns it.
- `REQUEST_PERMISSION` is a proposal, not a grant. It creates no `Action`, no delivery, and no action ID.
- `permission_denied` resolves to `SILENCE`.
- `EXPLORE`, `START_TASK`, `WAIT`, and `SILENCE` never execute a delivery.
- A denied decision cannot call the delivery callback.

`IMPLEMENTED` and `TESTED` — PR-05, PR-07.

## 6. SILENCE behavior

Silence is a valid terminal result.

- Companion disabled: `SILENCE`, reason `feature_disabled`, no delivery, no companion outcome, no companion state write.
- Presence disabled: policy returns `SILENCE` (`feature_disabled`), no delivery.
- Duplicate event: `SILENCE`, reason `duplicate_event`, no second delivery, no additional outcome or revision.
- Dismissed / expired / queue-full: `SILENCE`, no delivery; one truthful `REJECTED` outcome records that the event was evaluated.
- Silence creates no user-visible notice and no action ID.

The pre-existing Exploration → `PresenceService` notice path remains unchanged when the Companion feature is disabled; it is not part of the new Companion presence loop and writes no Companion state.

`IMPLEMENTED` and `TESTED` — PR-06, PR-08, PR-09.

## 7. Idempotency

- The presence event ID is deterministic: `SHA-256("companion:presence:" + notice_id)`.
- `companion_outcomes.event_id` has a database `UNIQUE` constraint.
- A duplicate event is detected before any delivery attempt; no second notice, outcome, or revision is produced.
- The existing in-memory Presence deduplication and durable `notification_status` checks remain in force.

`IMPLEMENTED` and `TESTED` — PR-09.

## 8. Failure isolation

- `Runtime._publish_exploration_question_with_companion()` wraps event assembly and recording. A failure is logged and returns `False`; it never propagates into the conversation path.
- A failure while recording presence bookkeeping does not alter the already-persisted conversation turn or its existing companion outcome/state.
- The outcome transaction remains atomic: no partial outcome or state row survives a failed commit.
- No notice is published if event assembly or policy evaluation fails.

`IMPLEMENTED` and `TESTED` — PR-10.

## 9. Observability

Bounded diagnostics use IDs, reason codes, action types, and statuses:

- presence event received (`event_id`, `notice_id`);
- decision selected (`action`, `reason`, `allowed`);
- presence silenced (`event_id`, `reason`);
- duplicate ignored (`event_id`, `reason`);
- outcome recorded (`event_id`, `status`, `published`);
- processing failure isolated (exception logged by the runtime boundary).

No conversation text, prompt content, memory content, chain-of-thought, or sensitive user data is logged.

`IMPLEMENTED` — logging in `companion_slice.py` and `runtime.py`.

## 10. Tests

`tests/test_companion_presence.py` uses temporary SQLite databases, a fake provider, and no network:

| Test | Verifies |
|---|---|
| PR-01 | The presence slice is triggered by the existing saved-question / Runtime notice boundary. |
| PR-02 | A real question becomes a typed `CompanionEvent` and is processed to a durable outcome. |
| PR-03 | Context items have explicit `CONFIRMED` / `CURRENT` / `MISSING` classifications, with no inference. |
| PR-04 | The decision and policy result are deterministic for the same evidence. |
| PR-05 | The existing `InteractionPolicy` permits and rejects the correct actions; denied decisions never execute. |
| PR-06 | Silence is terminal and causes no delivery; disabled Companion writes nothing. |
| PR-07 | Requesting permission grants nothing and executes nothing. |
| PR-08 | With Companion disabled, the new Companion presence loop is inactive and writes no Companion state. |
| PR-09 | Duplicate event IDs produce one notice, one outcome, and one revision. |
| PR-10 | Presence bookkeeping failure does not corrupt an already-persisted conversation. |
| PR-11 | No unsupported claims about user activity, location, mood, availability, monitoring, or offline experience. |
| PR-12 | Full Runtime boundary: fake provider, real turn persistence, real presence delivery, durable outcome/state. |

## 11. Actual limitations

- Presence is **event-driven only**. It reacts to a saved Exploration question at the existing delivery boundary.
- There is **no continuous presence**, no user-return detection, no monitoring, no scheduler, and no background observation.
- The only user-facing output is the existing bounded `PresenceService` notice, delivered at the foreground CLI prompt boundary. No notification system, popup, sound, or proactive chat was added.
- The Companion presence loop is woven into the pre-existing Exploration → Presence path only when `companion.enabled = true`. The legacy path remains for disabled Companion.
- The decision is deterministic; there is no LLM-based judgment of whether to speak.
- The user's availability, mood, location, and intentions remain `MISSING`; they are never inferred.
- No memory retrieval, cognition confirmation, embeddings, vector store, new database, new dependency, emotion engine, or avatar/voice behavior was added.
- `companion.enabled` remains `false` by default (`config.toml` and `config.py` unchanged).
- No commit was created.
