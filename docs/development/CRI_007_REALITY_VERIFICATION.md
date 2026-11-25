# CRI-007 — Reality Verification

**Status:** Reality verification complete in Turn 9. Not a feature-development turn.
**Code changed:** `src/sylphy/companion_slice.py` (truthful outcome detail text only).
**Evidence script:** `tests/reality_test.py` (live experiment, no fakes for available providers).
**Docs:** `docs/development/CRI_007_REALITY_VERIFICATION.md` (this file).
**No commit created.**

## 1. Audit Findings

### 1.1 What was audited

| Area | Finding |
|---|---|
| Runtime integration | `Runtime._publish_exploration_question_with_companion` routes saved Exploration questions through the companion pipeline (presence + autonomy + initiative). |
| Companion feature flags | `companion.enabled`, `autonomy.enabled`, `initiative.enabled` — all default OFF in config.toml and code. |
| Presence delivery mechanism | `PresenceService.publish()` appends to an in-memory `deque`. `_deliver_presence()` in `app.py` consumes the queue at the CLI foreground prompt boundary (`_prompt_user` before `input()`). |
| Actual delivery consumer | CLI only: `_deliver_presence` prints `\n[SYLPHY PRESENCE — title] content` to terminal. No GUI, no API endpoint, no persistent message store for delivery confirmation. |
| InvestigationExecutor | `start_investigation()` → `InvestigationExecutor.execute()` requires `search_provider`, `web_reader`, and `analyze` (model provider). All three must be configured. |
| Local model/provider | Ollama at `http://127.0.0.1:11434`, model `jarvis-uncen:latest` (3.4B gemma4). **Running and responsive.** |
| Web search configuration | `BraveSearchProvider` requires `BRAVE_SEARCH_API_KEY`. **Not configured.** `web_search.enabled` and `web_reader.enabled` are not in config.toml (default false). |
| Permission conditions | Evaluated via `PolicyConditions` passed at call time. No persistent permission store. |
| Outcome persistence | `companion_outcomes` table with UNIQUE `event_id`; `companion_state` singleton. Atomic outcome+state commit. |
| Restart/recovery | `recover_investigations()` on startup; `restore_pending_presence()` rebuilds Presence queue from durable queued notifications. |
| Repository modifications | All Turn 6/7/8 changes present and unmodified. |

### 1.2 Why live delivery was not verified in Turn 8

Three independent blockers:

1. **No Brave Search API key** (`BRAVE_SEARCH_API_KEY` env var empty). `BraveSearchProvider` raises `SearchCredentialsError` without it. `web_search.enabled`/`web_reader.enabled` are not enabled in config.toml. → `InvestigationExecutor` cannot execute → no completed investigation → no initiative trigger.
2. **No actual user-facing delivery consumer exercised.** `_deliver_presence()` in `app.py` is called only from `_prompt_user()` (blocking `input()`). Turn 8 tests used fakes and never drove the CLI prompt. → no message was ever displayed to a user.
3. **Outcome truthfulness defect.** `record_initiative` reported `OutcomeStatus.SUCCEEDED` when `presence.publish()` returned True — but `publish()` only appends to an in-memory queue, it does not deliver to the user. Same issue existed in `record_presence_event` (detail said "delivered at an observed foreground boundary" when only queued).

### 1.3 Fixes applied

- `src/sylphy/companion_slice.py`: Changed outcome detail text in `record_presence_event` from "Presence notice delivered at an observed foreground boundary." to "Presence notice queued for delivery at an observed foreground boundary."
- `src/sylphy/companion_slice.py`: Changed outcome detail text in `record_initiative` from "Bounded initiative response delivered." to "Bounded initiative response queued for presence delivery."
- No architecture changes, no new frameworks, no permission expansion.

## 2. Capability Verification Matrix

| Capability | Implemented | Unit Tested | Runtime Integrated | Live Verified |
|---|---|---|---|---|
| Presence | YES | 12 tests (PR-01…PR-12) | YES | YES (queue publication) |
| Bounded Autonomy | YES | 16 tests (AU-01…AU-16) | YES | YES (decision only) |
| Investigation | YES | existing tests | YES | BLOCKED (no web search key) |
| Purposeful Initiative | YES | 16 tests (IN-01…IN-16) | YES | BLOCKED (no completed investigation) |
| Delivery (queue) | YES | existing tests | YES | YES (queue publication) |
| Delivery (user-facing) | YES (CLI) | NOT TESTED | PARTIAL | NOT VERIFIED (requires CLI prompt) |
| Persistence | YES | existing tests | YES | YES (SQLite) |
| Recovery | YES | existing tests | YES | YES (restart tested with fakes) |
| Permission enforcement | YES | 16 tests (AU/IN) | YES | YES (decision-level, fakes) |

Status key: **VERIFIED** = confirmed with real execution; **PARTIALLY VERIFIED** = some stages real, others simulated; **TESTED WITH FAKES** = unit/integration tests with fakes only; **NOT VERIFIED** = not confirmed with real execution.

## 3. End-to-End Evidence — Live Experiment

### 3.1 Experiment environment

| Property | Value |
|---|---|
| Host | Windows 11, PowerShell |
| Python | 3.10.11 (D:\CWDI\Sylphy_Archive\Sylphy\.venv) |
| Ollama host | http://127.0.0.1:11434 |
| Model | jarvis-uncen:latest (gemma4-4.6B Q4_K_M, 3.4GB) |
| Ollama status | RUNNING (status 200 on /api/tags) |
| Network | Available (api.search.brave.com:443 reachable) |
| BRAVE_SEARCH_API_KEY | NOT CONFIGURED |
| Database | Temporary SQLite (deleted after experiment) |

### 3.2 Configuration used (in-code, config.toml unchanged)

| Flag | Value |
|---|---|
| companion.enabled | true (code override; config.toml: false) |
| autonomy.enabled | true (code override; config.toml: absent/default false) |
| initiative.enabled | true (code override; config.toml: absent/default false) |
| presence.enabled | true (constructor arg; config.toml: true) |
| exploration.enabled | false (config.toml: false) |
| web_search.enabled | false (config.toml: absent) |
| web_reader.enabled | false (config.toml: absent) |

### 3.3 Exact trigger

Saved Exploration question (durably persisted in SQLite):
- ID: `reality-question-1`
- Question: "What are the benefits of urban trees?"
- Source session: real session with user + assistant messages
- Status: pending, expires in 3 days

### 3.4 Stage-by-stage results

#### Stage 1 — Presence Publication: VERIFIED ✅

```
Runtime._publish_exploration_question_with_companion(saved)
  -> presence_notice_event (deterministic event id)
  -> build_presence_context (CONFIRMED/CURRENT/MISSING evidence)
  -> propose_presence_decision (SPEAK proposal)
  -> CompanionPolicy -> InteractionPolicy
  -> record_presence_event (execute callback)
  -> PresenceService.publish() -> True (queued)
  -> Outcome: SUCCEEDED, detail: "Presence notice queued for delivery..."
  -> companion_outcomes row persisted
```

Result: `published=true`, `pending_queue_size=1`, outcome recorded durably.

#### Stage 2 — Bounded Autonomy Decision: VERIFIED ✅

```
AutonomyDecision (deterministic):
  decision_id: 6fdbad539bb730a60d980e31a95bbdf889a511348fc6cf364e893f16de0960c2
  trigger_event_id: f9ad778dcd66ad778a2b585793c3a7e5b787dea534b4b9395571711a977ba366
  proposed_action: EXPLORE
  action_category: investigation
  required_permissions: ["investigation_search"]
  evidence: ["reality-question-1"]
  reason: "A delivered saved exploration question is eligible for one bounded autonomous investigation."
```

Result: deterministic proposal generated correctly through the real pipeline.

#### Stage 3 — Investigation Execution: BLOCKED ❌

Blocker: `BRAVE_SEARCH_API_KEY` not configured. `BraveSearchProvider` requires it. `web_search.enabled`/`web_reader.enabled` not enabled in config.toml. `InvestigationExecutor.execute()` requires `search_provider` and `web_reader`.

No real investigation execution possible without search credentials.

#### Stage 4 — Purposeful Initiative Decision: BLOCKED ❌

Blocker: no completed investigation (stage 3 blocked). No `InvestigationResult` exists to evaluate. `propose_initiative_decision` cannot be exercised with a real result.

#### Stage 5 — Delivery: PARTIALLY VERIFIED ⚠️

| Question | Answer |
|---|---|
| Does Presence publish to an in-memory queue? | YES — `PresenceService._pending` deque |
| What consumes that queue? | `_deliver_presence()` in `app.py`, called from `_prompt_user()` before `input()` |
| Is a message actually displayed to the user? | NOT IN THIS EXPERIMENT — requires CLI prompt interaction |
| Does delivery occur only at an observed foreground boundary? | YES — only before blocking `input()` |
| Can messages be lost during Runtime shutdown? | YES — in-memory queue, no persistence of pending items |
| Queued vs delivered vs acknowledged? | Partially: exploration notifications marked delivered in storage; initiative events not marked |
| SUCCEEDED after actual delivery or publication? | Publication only (fixed detail text to say "queued") |

### 3.5 Actual result

| Stage | Verdict | Evidence |
|---|---|---|
| Presence publication | VERIFIED | `published=true`, queue size 1, outcome row persisted |
| Autonomy decision | VERIFIED | deterministic EXPLORE decision with permissions and evidence |
| Investigation execution | BLOCKED | no Brave Search API key |
| Initiative decision | BLOCKED | no completed investigation result |
| User-facing delivery | NOT VERIFIED | requires CLI prompt; not driven in experiment |

## 4. Failure Boundaries

The following failure boundaries are covered by existing unit/integration tests (fakes):

| Failure mode | Test coverage | Live tested |
|---|---|---|
| Local model unavailable | `test_ollama_integration` (integration, conditional) | YES (model available) |
| Investigation provider unavailable | fakes in autonomy tests | NO (no search key) |
| Search unavailable | no API key configured | N/A (blocked) |
| Timeout | `InvestigationExecutor` timeout logic | NOT TESTED LIVE |
| Cancellation | `cancel_investigation`, `InvestigationPreempted` | NOT TESTED LIVE |
| Incomplete evidence | IN-03, IN-04 (fakes) | NOT TESTED LIVE |
| Permission denied | AU-04, AU-09, IN-09 (fakes) | NOT TESTED LIVE |
| Delivery consumer unavailable | N/A (CLI only) | NOT TESTED LIVE |
| Duplicate event | AU-07, IN-05 (fakes) | NOT TESTED LIVE |
| Duplicate initiative | IN-05, IN-06 (fakes) | NOT TESTED LIVE |
| Persistence failure | AU-13, IN-13 (monkeypatch) | NOT TESTED LIVE |
| Runtime restart | AU-14, IN-14 (fakes) | NOT TESTED LIVE |
| Companion disabled | AU-01, AU-16, IN-15 (fakes) | NOT TESTED LIVE |
| Autonomy disabled | AU-01, AU-16 (fakes) | NOT TESTED LIVE |
| Initiative disabled | IN-01, IN-15 (fakes) | NOT TESTED LIVE |

## 5. Delivery Boundary — Final Determination

**Presence publish** (`PresenceService.publish()`): appends to in-memory `deque`. Returns True if queued. NOT delivery.

**Presence delivery** (`_deliver_presence()` in `app.py`): called from `_prompt_user()` before `input()`. Iterates pending queue, calls `runtime.deliver_presence(event)` which:
- For exploration notifications: calls `store.mark_exploration_notification_delivered()` then `presence.dismiss()`
- For other events (initiative): calls `presence.dismiss()` only (no storage marking)
- Prints `\n[SYLPHY PRESENCE — title] content` to terminal

**Actual user display**: requires CLI foreground prompt. No GUI, no API, no persistent message store.

**Message loss on shutdown**: YES — pending queue is in-memory only.

**Outcome truthfulness**: `SUCCEEDED` now means "queued for presence delivery" (detail text fixed), not "delivered to user."

## 6. Test Results

| Suite | Result |
|---|---|
| Full test suite | **461 passed, 1 deselected** |
| Integration suite | **1 passed, 461 deselected** |
| Live experiment (reality_test.py) | Stage 1 VERIFIED, Stage 2 VERIFIED, Stage 3 BLOCKED, Stage 4 BLOCKED, Stage 5 PARTIALLY_VERIFIED |

Full-suite movement: unchanged from Turn 8 (445 → 461 with Turn 8's 16 tests). Truthfulness fix changed detail strings only, no test outcomes affected.

## 7. What SYLPHY Can Actually Do (Reality)

| Capability | Reality |
|---|---|
| React to a saved Exploration question through Presence | VERIFIED (queue publication) |
| Make a deterministic bounded autonomy decision | VERIFIED (EXPLORE proposal) |
| Execute a bounded investigation with real search | BLOCKED (no API key) |
| Make a purposeful initiative decision on a real result | BLOCKED (no completed investigation) |
| Display a Presence message to a user | NOT VERIFIED (requires CLI prompt) |
| Persist outcomes and state durably | VERIFIED (SQLite, tested live) |
| Recover across Runtime restart | TESTED WITH FAKES (not live) |

## 8. What SYLPHY Still Cannot Do (Reality)

- No real web search (no Brave API key)
- No investigation execution without search
- No initiative on a real investigation result
- No actual user-facing message display (CLI prompt not driven)
- No persistent delivery confirmation (initiative events not marked delivered)
- Messages can be lost on shutdown (in-memory queue)

## 9. Remaining Limitations

1. **Live investigation requires `BRAVE_SEARCH_API_KEY`** — not available in this environment.
2. **Live user-facing delivery requires driving the CLI prompt** — not automated in tests.
3. **Initiative events are not marked delivered in storage** — only exploration notifications are.
4. **All failure-boundary live tests are NOT TESTED LIVE** — only covered with fakes.

## 10. Repository Status

- Branch `master`; HEAD `2cdf98d81ca062601dabc25e36edc99af00521bc` (unchanged)
- No commit created
- `config.toml` unchanged (all feature flags preserve original values)
- `git diff --check` clean
- Compile clean
- Full suite: 461 passed, 1 deselected
- Pre-existing Turn 6/7/8 modifications preserved

---

*End of CRI-007.*
