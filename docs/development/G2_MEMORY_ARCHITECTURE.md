# SYLPHY G2 Memory Engine — Architecture Audit and Design

**Status:** Original sections record the Task 05 audit baseline and proposal. The G2-A implementation status is recorded at the end of this document.

## 1. Current architecture

Memory has two separate layers:

1. `Memory` / `MemoryService` in `src/sylphy/memory.py` define a small service contract over a repository. A confirmed memory contains ID, content, free-form category, created/updated timestamps, optional source session/message IDs, optional confidence, and status.
2. `SQLiteStore` in `src/sylphy/storage.py` persists sessions, messages, confirmed memories, cognition candidates, and cognition tasks. `ContextBuilder` in `src/sylphy/context.py` asks a `MemoryRetriever` for relevant confirmed memories during each conversation turn.

`CognitionService` in `src/sylphy/cognition.py` is a separate, optional post-turn proposal path. It analyzes bounded recent messages, may include retrieved confirmed memories, and persists validated candidates as pending. A user must confirm or reject a candidate; analysis does not directly write confirmed memories.

There is no distinct Memory Manager, consolidation service, memory graph, embedding store, or memory summarizer in the current code. `SQLiteStore` currently combines conversation, memory, cognition, exploration, and investigation persistence responsibilities.

## 2. Actual data flow

The actual ordering differs from the supplied conceptual flow: memory retrieval happens before inference, while candidate capture happens asynchronously after a completed turn.

```text
User input
  → Runtime.run_conversation()
  → ConversationService.respond()
      → load session history
      → ContextBuilder.build()
          → search confirmed memories from current input + recent user turns
          → fit selected memories/history into context budgets
      → OllamaProvider.reply(context)
      → persist completed user/assistant pair with SQLiteStore.append_turn()
  → Runtime best-effort enqueues CognitionTask after persistence
  → CognitionWorker loads source messages and invokes CognitionService.analyze()
  → CognitionService validates model JSON and stores pending candidates
  → user reviews `/cognition list`, then confirms or rejects
  → confirmation calls MemoryService.create() and transitions candidate to confirmed
  → future ContextBuilder builds may retrieve that confirmed memory
```

| Stage | Implementation and inputs/outputs | Persistence and failure behavior | Evidence/tests |
|---|---|---|---|
| Session/history | `SQLiteStore.messages(session_id)` returns messages in insertion (`rowid`) order. `ConversationService.respond()` checks the session and reads history. | `messages.session_id` has `ON DELETE CASCADE`; deleting a session deletes its messages. | `src/sylphy/storage.py` (`messages`, `delete_session`); `src/sylphy/core.py::ConversationService.respond` |
| Retrieval query | `ContextBuilder.build(history, user_text)` builds a query from up to three latest non-empty user messages among the last eight history entries, plus current input, deduplicated and bounded to 1200 characters. | A retrieval exception is logged and treated as no memories, so conversation proceeds. | `src/sylphy/context.py::ContextBuilder.build`; `tests/test_memory.py::test_memory_retrieval_uses_recent_user_turns_for_short_followups`, `test_context_continues_when_memory_retrieval_fails` |
| Memory selection | `MemoryService.retrieve()` delegates to `SQLiteStore.search_memories()`. Exact case-insensitive tokens over length two score content, category, full-term coverage, and a phrase; ties use updated time then ID. | Only `confirmed` status participates. ContextBuilder caps item count and estimated memory tokens; oversized memories are skipped. | `src/sylphy/memory.py::retrieve`; `src/sylphy/storage.py::search_memories`; `src/sylphy/context.py::build`; search/budget tests in `tests/test_memory.py` |
| Context assembly | `ContextBuilder` assembles merged system+identity instructions, optional visual safety/context, bounded memory block, selected historical messages, and current user text. | At the Task 05 audit baseline, memory was described as user-provided untrusted data; Task 08 now labels it generally as untrusted contextual data because provenance varies. Identity/system/current text are required; if required context exceeds overall budget, `ContextOverflowError` is raised. | `src/sylphy/context.py::_render_system_prompt`, `build`; `tests/test_memory.py` |
| Inference and turn persistence | `ConversationService.respond()` calls the configured `ModelProvider.reply()` then creates and atomically inserts the user/assistant message pair through `append_turn()`. | Cancellation, provider errors, or empty output prevent completed-turn persistence. `append_turn` validates the pair and writes both messages in one SQLite connection transaction. | `src/sylphy/core.py::ConversationService.respond`; `src/sylphy/storage.py::append_turn`; `tests/test_foundation.py` |
| Candidate creation | After the service returns a persisted `Turn`, `Runtime.run_conversation()` best-effort enqueues it when the Cognition worker exists. The worker later loads the source messages and calls `CognitionService.analyze()`. | Enqueue/analyze failures do not invalidate the completed conversation; tasks have bounded retries and durable states. In the inspected `config.toml`, both Cognition and Memory are enabled; Cognition enablement is configurable. | `src/sylphy/runtime.py::run_conversation`, `CognitionWorker`; `src/sylphy/app.py` wiring; `config.toml`; `tests/test_cognition_worker.py` |
| Candidate review/confirmation | `CognitionService` validates JSON candidate fields/category/confidence, filters normalized exact duplicates of retrieved confirmed memory content, then calls `create_candidates`. `/cognition list|confirm|reject` is the application control. | Candidates remain pending until explicit action. Confirm creates a confirmed Memory with source session and the last source message ID, then transitions the candidate. Reject only transitions candidate state. | `src/sylphy/cognition.py::analyze`, `confirm`, `reject`; `src/sylphy/app.py` cognition command handling; `tests/test_cognition.py` |
| Manual memory management | `/memory add`, `list`, `search`, `update`, and `delete` use `MemoryService`; add associates current session and latest user message when available. | Deletion requires typing `DELETE <id>`. CRUD errors are shown to the user. | `src/sylphy/app.py::_memory_command`; `tests/test_memory.py::test_memory_cli_add_search_and_confirmed_delete` |

## 3. Existing capabilities and memory classification

| Concept | Current support | Audit conclusion |
|---|---|---|
| Working memory | Recent messages from the current session, context-budget selection, plus temporary visual context for one request. | Implemented as bounded prompt context, not as a separate mutable working-memory store. No cross-session active-task object exists. |
| Episodic memory | Full session transcripts are stored; Cognition may propose an `important_event` category. | Partially represented by conversation history. There is no cross-session event index or automatic event recall. `important_event` is just a category string, not an episodic subsystem. |
| Semantic memory | Confirmed Memory records with content/category/confidence/provenance, lexical search, context inclusion, and manual CRUD. | Implemented in a small practical form. No schema-enforced taxonomy or fact validity interval exists. |
| Procedural memory | A user may manually store workflow instructions as free-form memory; Cognition categories do not include a dedicated procedure type. | Not modeled or retrieved differently. Any behavior depends on lexical relevance and the model interpreting the content. |

The current implementation is best described as **session history plus a manually managed, lexical set of confirmed semantic notes**, with an optional model-generated candidate inbox.

## 4. Quality, safety, and gaps

- **Duplicates (Task 05 baseline):** Manual creation had no uniqueness constraint. G2-A now prevents new normalized exact duplicates per category, but preserves any duplicates already in existing databases. Candidate persistence deduplicates normalized exact content within a category across source turns. Neither catches semantic paraphrases.
- **Contradictions and corrections:** Updating a memory edits the same record, but there is no contradiction detector, version history, explicit supersession link, or capture of why it changed. A correction is not automatically connected to prior memories. A user can manually update or delete one.
- **Outdated facts:** `updated_at` exists but is not used as an expiry policy. No validity start/end or decay exists. Retrieval gives recency only as a tie-breaker, not a relevance boost.
- **Incorrect retrieval/context pollution:** Search uses exact lexical tokens and category terms; it is deterministic and explainable but may miss synonyms, multilingual variants, entity aliases, and conceptual relevance. The query can combine the last user topics with the current one. A falsely matching note may enter the prompt. The item and block count/token budgets limit exposure.
- **Growth:** Memory CRUD has no maximum record count, archival, or expiration. Search loads all confirmed memories into Python and ranks them in memory, so cost grows linearly with confirmed memory count.
- **Sensitive data:** Cognition prompt asks for stable, non-sensitive candidates, but this is a model instruction rather than a programmatic content filter. Manual `/memory add` accepts arbitrary text. SQLite memory content is not encrypted by this application. There is no sensitivity label, redaction, or retention policy.
- **Deletion/provenance (Task 05 baseline):** Memory deletion is available with CLI confirmation. Source IDs are optional fields and have no foreign-key constraints; deleting a source session does not cascade to the memory. This preserves a source identifier but may leave a dangling reference. No source message snapshot is stored with memory. G2-A adds an explicit origin field; it does not change source-ID behavior.
- **Confidence/timestamps:** Confidence is optional and range-checked 0–1; it is neither calibrated nor used by ranking. Created and updated timestamps are UTC-aware ISO strings. Updated timestamp changes on update. Source session/message IDs are optional.
- **Status:** Ordinary memories are inserted as `confirmed`; candidates are separately stored with `pending`, `confirmed`, or `rejected`. `Memory.status` exists, but there is no general memory lifecycle beyond the current confirmed records.
- **Error behavior:** Retrieval failure does not block conversation. Candidate analysis and persistence failures are visible in task state/logs and do not roll back completed turns. Manual storage errors are handled at CLI command level only for expected validation/not-found cases.

## 5. Reference-project findings

The named Mark VII and Animama source repositories, documentation, and tests were **not present in the canonical repository or the inspected adjacent `D:\CWDI` project directories**. No external project source was accessed for this audit. Therefore, detailed source-backed findings by class/function cannot be verified here.

Available SYLPHY materials say:

- `SPRINT_10_EXTRA_RELEASE_NOTES.md` states that Mark VII task persistence/checkpoint and recall/retrieval patterns were examined, and that SYLPHY adapted principles to its SQLite and worker architecture rather than porting its task engine.
- The same notes state that Animama emotion-state validation, memory separation, and avatar/runtime controller boundaries were examined, while the current SYLPHY change was presentation-state validation only.
- `src/sylphy/context.py` contains a comment that its on-demand recall query adapts a Mark VII principle to a single-pass ContextBuilder.

These are **SYLPHY-authored provenance statements**, not independently inspectable evidence of the reference projects' behavior. They support only a cautious applicable lesson: keep retrieval demand-bounded and distinct from the conversation transcript; avoid copying an architecture without source validation. No Animama memory behavior or emotion/memory integration can be attributed from available source evidence. The reference-project conclusions remain unverified until source trees or pinned source links are supplied.

## 6. Proposed G2 architecture

Keep the existing `MemoryService`, `SQLiteStore`, `CognitionService`, and ContextBuilder boundary. G2 should improve lifecycle and retrieval in place before introducing new modules.

Recommended minimal responsibility split:

- **MemoryService (existing):** validation, CRUD, and explicit lifecycle operations. Add only small APIs needed by the approved G2 scope.
- **SQLiteStore (existing):** schema/migrations, durable memory records, search indexes, and transactional updates. Keep session history and memory records distinct.
- **Memory retrieval (existing method/contract):** deterministic bounded lexical selection. It can remain `search_memories` behind `MemoryService.retrieve`; a separate class is not justified until a second retrieval implementation exists.
- **ContextBuilder (existing):** remains the only conversation prompt assembly point; it applies memory item/token budgets and untrusted-data framing.
- **CognitionService/worker (existing):** optional proposal generation only. It must not auto-confirm or mutate existing confirmed memories.
- **Consolidation:** defer model-driven consolidation. Add only explicit user correction/update and, if needed, deterministic duplicate review. A consolidation component now would duplicate the current small service without an established policy.
- **Decay/expiration:** defer. Current requirements have no evidence-based retention semantics; first add timestamps/provenance and user controls, then decide expiration policy.
- **Conflict resolution:** initial policy should be explicit user correction/update, with the old content optionally retained as superseded history if auditability is required. Do not automatically choose between contradictory facts.

This preserves SQLite/local-first operation and works without model inference in unit tests. No embeddings or external dependencies are justified by the current corpus or hardware.

## 7. Proposed data model

The existing `memories` table already covers the minimum useful core. Suggested G2 additions should be limited to fields that support a concrete acceptance criterion:

| Field | Proposed type | Required? | Purpose/example | Existing? |
|---|---|---:|---|---|
| `id` | TEXT stable UUID | Yes | `...` record identity | Yes |
| `content` | TEXT | Yes | `Prefers concise answers` | Yes |
| `category` | TEXT | Yes | `preference`, `project`, `goal`, `important_event`, `procedure`, `other` | Yes, free-form; Cognition allowlist is narrower |
| `status` | TEXT enum/check | Yes | `confirmed`, possibly `superseded` or `archived` only if G2 explicitly needs those states | Yes, default `confirmed`; unconstrained for memories |
| `created_at` | UTC ISO timestamp | Yes | Initial capture time | Yes |
| `updated_at` | UTC ISO timestamp | Yes | Last user edit | Yes |
| `source_session_id` | nullable TEXT | No | Session that supplied it | Yes |
| `source_message_id` | nullable TEXT | No | Specific message (today confirmation records last candidate source message) | Yes |
| `confidence` | nullable REAL 0–1 | No | Candidate estimate, not calibrated probability | Yes |
| `importance` | integer 1–5 or nullable | No, defer | Explicit user priority if retrieval needs it | No; avoid adding until a user-facing importance policy exists |
| `valid_from` / `valid_until` | nullable UTC timestamp | No, defer | Time-bounded fact validity | No; requires clear correction/expiry semantics |
| `supersedes_id` | nullable TEXT | No, optional future | Link replaced fact to prior record | No; add only if retaining correction history is required |
| `sensitivity` | enum/label | No, open decision | Could support review/retention controls | No; do not imply current model prompt is a filter |
| `related_entities` | normalized child table or JSON | No, defer | Entity joins/aliases | No; no demonstrated use yet |
| retrieval metadata | derived score/query | No | Explain ranking at runtime | No; scores can be computed without persistence |

**Recommended G2 baseline:** retain the current nine columns; constrain category/status values only after deciding compatibility with existing free-form user categories. Add a correction/supersession relation only if the roadmap requires history to survive an update. Avoid importance, expiry, entity tables, and sensitivity taxonomy in the first storage migration.

## 8. Retrieval strategy

The current lexical retrieval is adequate for an initial deterministic baseline and already has tests. G2 should make its contract clearer rather than replace it with an LLM pipeline:

1. Query from the current user message and a small bounded number of recent user messages, as now.
2. Search only active/confirmed memory records.
3. Tokenize consistently; exact term match avoids substring false positives.
4. Rank with explainable weights: content term coverage first, category match secondary, recency only as a tie-breaker. Keep the current score initially, document its behavior, and expose score breakdown in tests rather than persisting it.
5. Return at most configured `max_items`; ContextBuilder applies memory token budget and total context budget.
6. Preserve prompt order/provenance if a memory needs a correction marker. Never silently rewrite retrieved content.
7. On retrieval failure, continue without memory; on no match, continue normally.
8. If an explicit correction is present, do not auto-resolve against stale data. G2 should first make correction/update a user-confirmed operation; automatic conflict resolution is a later decision.

No vector store is recommended until measured retrieval misses show lexical matching is inadequate.

## 9. Memory lifecycle proposal

| Stage | Trigger/responsibility | Required data | Failure behavior |
|---|---|---|---|
| Capture | Manual `/memory add`, or opt-in Cognition candidate after a completed turn | Content, category, source IDs where available, confidence if model proposed | Invalid empty content/range rejected; conversation remains completed if background cognition fails |
| Validation | `MemoryService` for manual input; strict Cognition schema validation for proposals; user review before confirmation | Category/status contract; source provenance | Invalid candidate is rejected as a whole; no partial insert; no automatic confirmation |
| Storage | `SQLiteStore` transaction | Stable ID, timestamps, status, provenance | Roll back failed write; report operation failure; do not alter conversation transcript |
| Retrieval | ContextBuilder per turn; MemoryService/SQLiteStore lexical search | Current query, limit, budgets, confirmed status | Log retrieval error and omit optional memory context |
| Update/correction | Explicit `/memory update <id> <content>` or confirmation workflow | Target ID, replacement content, update time; ideally correction provenance | Not-found/invalid input is reported. Never silently overwrite based on model inference |
| Consolidation | Not implemented; defer until explicit policy exists | Would require duplicate/conflict evidence and human review rules | No automatic merge/delete in G2 baseline |
| Expiration/deletion | Explicit confirmed `/memory delete <id>`; optional expiry only after policy decision | ID and confirmation | Cancel deletion if confirmation mismatch; no automatic expiry today |

## 10. G2 implementation roadmap

Likely paths below are based on current code; each task should be separately scoped and re-inspected before implementation.

### G2-A — Schema and storage foundation

- **Scope:** Decide if any new fields are required. Prefer no schema addition for a baseline; otherwise add a versioned, non-destructive SQLite migration for only approved fields/status values.
- **Likely files:** `src/sylphy/storage.py`, `src/sylphy/memory.py`, `tests/test_memory.py`, possibly `tests/test_foundation.py` for migration compatibility.
- **Dependencies:** Agree whether correction history/supersession is in scope.
- **Acceptance:** Existing DB opens without loss; new columns/defaults are verified; CRUD and restart behavior pass on temporary DBs.
- **Tests:** Empty/existing DB migration, rollback, CRUD, status filtering, provenance preservation, reopen.

### G2-B — Capture and provenance

- **Scope:** Make source attribution consistent for manual and confirmed candidate memories; add explicit user-facing category/source behavior only if needed. Candidate confirmation currently stores the assistant source message ID, so decide whether both source message IDs should be represented.
- **Likely files:** `src/sylphy/cognition.py`, `src/sylphy/memory.py`, `src/sylphy/app.py`, `src/sylphy/storage.py`, `tests/test_memory.py`, `tests/test_cognition.py`.
- **Dependencies:** Current schema decision and UX for selecting source/category.
- **Acceptance:** Every captured memory preserves available provenance, and no capture path silently fabricates missing source metadata.
- **Tests:** Manual capture source, cognition confirmation provenance, missing source, reject without memory write.

### G2-C — Retrieval and ContextBuilder integration

- **Scope:** Retain deterministic lexical search; define ranking, active-status behavior, and explanation contract; keep ContextBuilder the single assembly point with current and memory budgets.
- **Likely files:** `src/sylphy/storage.py`, `src/sylphy/memory.py`, `src/sylphy/context.py`, `src/sylphy/config.py`, `config.toml`, `tests/test_memory.py`, `tests/test_regression_g1.py`.
- **Dependencies:** G2-A status/category decisions.
- **Acceptance:** Stable ranked bounded results; current user text and identity remain intact; empty/failing retrieval remains non-blocking; no prompt pollution outside configured budgets.
- **Tests:** exact-token behavior, scoring/order, history-assisted follow-up, budget overflow, no results, repository exception.

### G2-D — Update, conflict handling, and consolidation

- **Scope:** Start with explicit user correction/update. If history is required, implement supersession rather than destructive merge; defer model-driven consolidation absent a deterministic review policy.
- **Likely files:** `src/sylphy/memory.py`, `src/sylphy/storage.py`, `src/sylphy/app.py`, `tests/test_memory.py`.
- **Dependencies:** Decision on retaining old facts and whether superseded records remain searchable.
- **Acceptance:** User corrections are explicit, traceable, do not create silent conflicting active entries, and can be undone/reviewed if history is included.
- **Tests:** update timestamps, preserve source, correction link/old status if approved, reject invalid transitions, duplicate handling.

### G2-E — Lifecycle, deletion, and regression

- **Scope:** Clarify deletion and retention controls, test cross-session behavior, status migration, context safety, and interaction with Cognition. Do not add automatic expiration without an agreed policy.
- **Likely files:** `src/sylphy/app.py`, `src/sylphy/memory.py`, `src/sylphy/storage.py`, `src/sylphy/config.py`, `README.md`, `tests/test_memory.py`, `tests/test_cognition.py`, `tests/test_cognition_worker.py`.
- **Dependencies:** G2-A through G2-D decisions.
- **Acceptance:** Users can list/search/update/delete confirmed memories, deletion confirmation is enforced, conversation/cognition behavior remains compatible, and docs match defaults.
- **Tests:** temporary DB lifecycle/restart, deletion confirmation, confirmed-only retrieval, candidate isolation, migration and full regression suite.

## 11. Risks

- A lexical search over all confirmed memories is simple but O(number of records) and may become slow as the table grows.
- No application-level encryption or sensitive-data classification exists; local storage alone does not guarantee the database is protected from other local processes/users.
- Automatic candidate generation is model-dependent. Strict parsing and user confirmation reduce risk but do not guarantee factual accuracy.
- The category field is permissive, while Cognition uses a fixed category allowlist; narrowing it may break manually created records or future candidate classes.
- Source message attribution during candidate confirmation is incomplete relative to the candidate's two-message provenance.
- Session deletion cascades message deletion, but memories keep source IDs without foreign-key enforcement. This preserves provenance identifiers but not their source content.
- Reference-project conclusions cannot be independently validated without their source repositories or pinned documentation.

## 12. Open decisions

1. Is G2 a second memory-engine milestone or a correction to an already implemented G3-style memory subsystem? Task labels and repository behavior should be reconciled before schema changes.
2. Should updates be destructive edits, or should prior versions remain as superseded records?
3. Should `procedure` and other categories be accepted by Cognition, or only manual entry?
4. Should memory capture always require explicit user confirmation? Current manual creation immediately confirms; Cognition candidates require confirmation.
5. Should a deleted session's memories remain with dangling provenance, be retained as identifiers, or be deleted only after a separate confirmation?
6. What local threat model and retention policy is expected for sensitive personal data?
7. Can Mark VII and Animama repositories/source references be supplied for the requested source-level comparison?
8. Should retrieval include an explicit list/trace of selected memory IDs for user observability?

## 13. Verification performed for this audit

- `python -m pytest tests/test_memory.py -q` — **12 passed**.
- No production source, test, configuration, or database files were modified.
- The existing uncommitted worktree was preserved; only this audit document and the reference decision log were changed by Task 05.

## 14. G2-A implementation update

### Actual schema change

The `memories` table now has a tenth column:

```sql
origin TEXT NOT NULL DEFAULT 'unknown'
    CHECK(origin IN ('unknown','legacy_unknown','user_provided','manual','confirmed_candidate'))
```

No importance, expiry, sensitivity, related-entity, or calibration fields were added. `confidence` remains nullable and bounded 0–1; no value is synthesized where none was supplied. Existing source session/message IDs, timestamps, status, and content remain unchanged. Cognition candidates remain in their separate `memory_candidates` table and retain their pending/rejected/confirmed status and source message IDs.

### Provenance behavior

- Existing databases gain `origin='legacy_unknown'` for every row whose origin was not previously recorded. No historical provenance is inferred.
- The generic MemoryService/storage creation API defaults to `unknown`; callers that know the source may set `user_provided`, `manual`, or `confirmed_candidate`.
- `/memory add` writes `manual` because the user explicitly entered the memory through that command.
- Cognition confirmation writes `confirmed_candidate`; pending generated proposals remain separate candidate rows until the user confirms them.
- `user_provided` is available only for a caller that has direct evidence the content was supplied by the user. The current Cognition pipeline does not claim this for generated candidate text because candidates can be derived from both user and assistant messages.
- Updates preserve the original `origin`; they do not imply a new source.

### Migration and compatibility

`SQLiteStore._ensure_memory_schema()` checks the existing schema and uses `ALTER TABLE ... ADD COLUMN` to add the constrained field in place. The operation is enclosed in a SQLite savepoint and rolls back on failure. It does not rebuild or delete the table. Reopening is idempotent because the column is detected. New databases create the same column with default `unknown`; legacy existing rows receive `legacy_unknown`.

Existing `Memory` positional construction remains compatible because the new dataclass field is appended with a legacy-safe default. Repository and service create methods accept the optional origin at the end of their signatures. Conversation messages and pending candidates are not rewritten by this migration.

### Exact duplicate and update behavior

Confirmed-memory creation now runs under `BEGIN IMMEDIATE` and returns an existing record instead of inserting a new one when both conditions match:

1. Category matches after trimming and case folding.
2. Content matches after Unicode case folding and collapsing all whitespace runs to one space.

This is exact normalized equality, not similarity or semantic deduplication. A duplicate returns the existing record unchanged, including its ID, provenance, timestamps, confidence, and origin. Different categories or any remaining content difference create distinct records. Pre-existing duplicate records are preserved and are not automatically cleaned up.

Updating a record to content/category that exactly matches another confirmed record raises `ValueError`; neither record is merged, removed, or overwritten. Updates preserve origin. No LLM or embedding dependency was introduced.

### G2-A tests and known limits

- Added temporary-database coverage for old-schema migration, field defaults, content/provenance preservation, pending candidates, conversation history, repeat initialization, and simulated migration rollback.
- Added tests for manual and confirmed-candidate origin, invalid origin, exact duplicate idempotence, category distinction, safe update collision behavior, deletion, and SQLite insert failure.
- `python -m pytest tests/test_memory.py tests/test_cognition.py -q` — **45 passed**.
- `python -m pytest -q` — **327 passed, 1 deselected**.
- `python -m compileall -q src/sylphy tests` — passed.

At the G2-A baseline, `user_provided` required a caller with evidence and was not auto-assigned by Cognition; pending generated candidates were distinguished by their separate table/status and Cognition-only creation path, not an additional candidate-origin column. Existing duplicates remained in place. Semantic deduplication and candidate source-message granularity remain outside scope. Correction-history behavior was added later in G2 Task 09 below.

## 15. G2 Task 07 — capture and provenance flow

### Verified capture lifecycle

1. `CognitionService.analyze()` parses model output into `MemoryCandidate` records with `pending` status and source session plus the user/assistant source message IDs from the analyzed turn.
2. `SQLiteStore.create_candidates()` persists candidates independently from confirmed memories. Reprocessing an identical candidate returns the existing candidate and does not reset its lifecycle status.
3. `/cognition list` exposes pending proposals. `/cognition confirm <id>` is the only application path that moves a candidate to confirmed. `/cognition reject <id>` moves it to rejected and creates no memory.
4. In SQLite, G2 Task 07 confirmation inserts or finds the exact confirmed memory and transitions the candidate within one `BEGIN IMMEDIATE` transaction. A failure in either operation rolls back both. Repeated confirmation returns the matching memory without creating a second one.
5. Confirmed memory retains `origin='confirmed_candidate'`, the source session, a backward-compatible singular `source_message_id` (the final source ID when present), and the complete ordered `source_message_ids` tuple.
6. `/memory add` continues to create a confirmed memory directly with `origin='manual'` and the current session/latest user-message source when available.

### Additional schema and migration

G2 Task 07 adds `source_message_ids TEXT NOT NULL DEFAULT '[]'` to the memory table. IDs are serialized as a JSON array in their original order. Legacy singular IDs are copied into a one-element JSON array; null legacy IDs become `[]`. This uses only provenance already present and does not infer missing IDs. The new column is added inside the existing provenance migration savepoint. Existing APIs remain source-compatible: the new optional tuple parameter is appended after existing arguments, the prior singular field remains available, and generic `MemoryService.create()` calls continue using the original five-argument repository contract unless new provenance metadata is explicitly requested.

### Atomic confirmation invariant

Previously, `CognitionService.confirm()` wrote a memory through `MemoryService` and then transitioned the candidate through a separate SQLite transaction. A failure during the second write could leave a pending candidate and a confirmed memory. The SQLite repository now performs both state changes on one connection/transaction. The legacy fallback remains for third-party repositories without the optional atomic method; those adapters retain their prior non-atomic behavior until they implement that method.

### Verification

- Candidate source tuple is preserved on confirmation; legacy singular provenance is retained.
- Simulated candidate-status update failure leaves the candidate pending and writes no memory; retry then confirms both.
- Rejection leaves confirmed memory storage unchanged.
- Repeated confirmation returns the same memory and does not add another record.
- `python -m pytest tests/test_memory.py tests/test_cognition.py -q` — **48 passed**.
- `python -m pytest -q` — **330 passed, 1 deselected**.
- `python -m compileall -q src/sylphy tests` — passed; `git diff --check` — passed (Git reported line-ending conversion warnings only).

Known limit: if exact duplicate content/category already exists, confirmation reuses that existing memory unchanged to honor the exact-duplicate safeguard. It does not merge new provenance into the existing record. Multi-source provenance for that already-existing memory therefore remains limited to its earlier source.

## 16. G2 Task 08 — retrieval and context integration

### Retrieval query and ranking

Conversation retrieval query construction remains in `ContextBuilder.build()`: take up to the last eight history messages, keep non-empty user messages, choose the last three, append the current user message, trim each, remove exact duplicate strings while preserving order, join with newlines, then keep the final 1200 characters. Cognition and Exploration also call `MemoryService.retrieve()` with their own bounded analysis input; they use the same store search rules.

`SQLiteStore.search_memories()` applies these deterministic rules:

1. Return no results for a blank query, a non-positive limit, or a query with no tokens longer than two characters.
2. Tokenize query, content, and category with Unicode `\\w+`; case-fold tokens and ignore tokens of length two or less. Query terms are unique in their first-seen order.
3. Search confirmed memory rows only. Pending/rejected candidate rows live in `memory_candidates`, not the `memories` query.
4. Score each memory by `2 × each matching content-token occurrence`, plus `1 × each matching category-token occurrence`; add 2 when content contains every query term; add 3 when all query terms appear consecutively as exact content tokens.
5. Sort by score descending, `updated_at` instant descending, then memory ID ascending.
6. Suppress exact normalized duplicates by `(casefolded content with whitespace collapsed, casefolded trimmed category)`, retaining the highest-ranked record. Continue walking ranked results until the requested number of unique memories is reached.

Category is a secondary lexical signal, not a filter or semantic type. Recent update time resolves equal-score ties; it does not add relevance score. There is no semantic or synonym matching.

### Task 08 fixes

- Before this task, `MemoryService.retrieve()` sorted repository results again using Python's process-randomized `hash(content)`. This discarded the repository's relevance order and was not stable across processes. It also suppressed duplicates after the repository had already truncated to `limit`, which could return fewer unique results than requested. It now delegates directly to the repository; ranking, tie-breaking, confirmed-only filtering, deduplication, and limit are kept in one place.
- Phrase bonus previously used substring matching on a joined token string, so a query token at the end of a longer word could receive a false phrase bonus. Phrase bonus now requires an exact consecutive token sequence.
- Duplicate suppression now happens after ranking but before limit enforcement. A pre-existing exact duplicate does not consume a result slot; the top-ranked copy is retained.
- ContextBuilder previously assigned the rendered prompt block before checking whether a proposed memory fit. If a later memory exceeded the budget, its text could remain in the final prompt despite being excluded from accounting. It now stores only the last accepted block.
- ContextBuilder independently caps retrieved results at its configured `memory_limit`, even if a custom retriever returns too many.
- The memory guard text now calls retrieved records untrusted contextual data, rather than claiming every record was user-provided. Memory body content remains unchanged.

### Context placement, limits, and provenance

System instructions and merged Identity remain the first system message. If temporary visual context is present, its safety guard follows; if memories are selected, their separate system message follows that guard. Chronological selected history follows, then optional temporary visual observation data, then the current user request. Memory IDs, origin, and source IDs stay in the returned `Memory` objects for application-level consumers; they are not copied into the model prompt because they are internal metadata.

The configured maximum is five memories and 512 estimated tokens in the checked-in `config.toml`; application construction passes these settings to both ContextBuilder and Cognition. ContextBuilder also enforces the overall model context budget using `CharacterTokenEstimator` (UTF-8 byte length divided by four, rounded up, at least one). A proposed memory is accepted only if the rendered memory block stays within the memory budget and required instructions/current input plus the memory block stay within the overall budget. Over-budget memories are skipped; the system/identity/current user input remain required. Selected memory consumes space otherwise available to history. Retrieval exceptions are logged and conversation continues without retrieved memory.

Only `confirmed` records are searchable. The retrieval service preserves provenance on returned Memory objects (`origin`, source session, singular and ordered source message IDs); context rendering intentionally includes content and category only. Memory content is labeled untrusted and the prompt says not to follow instructions contained in it. This is a prompt-level boundary, not a perfect security guarantee.

### Task 08 verification

- Focused retrieval/context tests include empty and low-information queries, exact-token and exact-phrase handling, category relevance, deterministic tie-break, duplicate suppression before limits, pending/rejected exclusion, context limit enforcement, rejected-over-budget memory exclusion, provenance preservation, untrusted memory labeling, and existing G1 context tests.
- `python -m pytest tests/test_memory.py tests/test_regression_g1.py tests/test_foundation.py -q` — **45 passed**.
- `python -m pytest -q` — **337 passed, 1 deselected**.
- `python -m compileall -q src/sylphy tests` — passed; `git diff --check` — passed (Git emitted line-ending conversion warnings only).

Known limitations: lexical scoring can miss paraphrases, synonyms, and cross-language equivalents; category matches can still contribute a result for a broad query; score weights are simple heuristics, not calibrated relevance. Search currently loads all confirmed memories into Python before scoring, so cost grows linearly with memory count. Context token estimation is approximate. Retrieved memories are not currently shown to users as citations or an explainability trace.

## 17. G2 Task 09 — explicit corrections and memory history

### Audit findings

- Before Task 09, `MemoryService.update()` changed the existing confirmed row in place. It retained the row ID and existing provenance, but replaced the old content without preserving it or recording the correction source.
- Explicit deletion requires the existing `/memory delete <id>` confirmation and physically removes the row. Retrieval already searches confirmed rows only; pending and rejected cognition candidates are stored separately.
- Exact normalized duplicates (case-folded content with collapsed whitespace plus case-folded category) are idempotent on creation. Updates reject an exact collision with another confirmed record. Distinct contradictory statements are not reliably detectable and are allowed to coexist.
- Candidate confirmation uses the same exact duplicate rule. It does not identify semantic conflicts or supersede other memories.

### Correction and lifecycle behavior

`/memory update <id> <content>` identifies one exact memory ID; missing IDs fail rather than selecting by text. A real content/category/confidence change snapshots the prior record into `memory_revisions` and updates the current row in one SQLite transaction. The existing memory ID remains stable. The current row retains its original source and origin; the revision stores that prior provenance plus separate correction origin, correction source session, optional correction message IDs, and revision timestamp. The CLI records a manual correction and current session ID; it does not invent a message ID because the slash command is not persisted as a conversation message. API callers may supply correction message IDs when actually known.

`/memory history <id>` lists prior snapshots chronologically. Normal list/search/context retrieval continues to use only current confirmed rows; revision snapshots are historical and never retrieved as current context. An exact duplicate collision still fails without creating history. A successful no-op update creates no revision. Storage failure during the current-row update rolls back the history insert as well.

Deletion remains explicit hard deletion. `memory_revisions.memory_id` references `memories(id)` with `ON DELETE CASCADE`, so deleting a memory intentionally removes its correction history too. No active/superseded status was added: this implementation treats the current row as current and revision rows as historical. Separate conflicting memories remain current until the user explicitly corrects a targeted row.

### Migration, compatibility, and verification

Migration is additive: initialization creates `memory_revisions` and its `(memory_id, revision_at, id)` index with `IF NOT EXISTS`. Existing confirmed memories, candidates, and conversation rows are not rewritten. Repeated initialization is safe. Existing update calls that provide no correction metadata retain the repository's prior call shape; repositories without history support can still update, while requesting history from them reports `NotImplementedError`.

Deterministic tests cover exact-target failures, duplicate collision, prior/current provenance, correction source, timestamp, historical retrieval exclusion, conflicting records coexisting, idempotent migration, rollback, deletion cascade, and CLI history. No LLM or semantic conflict detector is involved.

Known limitations: conflict detection is intentionally limited to exact normalized duplicate handling; semantic conflicts and temporal validity are not inferred. Legacy corrections cannot be retroactively reconstructed. Hard deletion removes revision history. The CLI can record only its current session, not a source message, for a slash-command correction.

## 18. G2 Task 10 — integration audit and final verification

### Final verified status

This section records implementation behavior verified in the canonical working repository at `D:\CWDI\SYLPHY_Sprint_10_plus_1_fixed\sylphy_work`. It does not treat planned architecture proposals above as implemented behavior.

- **Storage and migrations:** SQLite initialization uses additive, repeatable schema setup for legacy memory provenance, candidate identity, and memory revision history. The relevant temporary-database tests preserve legacy memory rows, candidates, and conversation messages; repeated initialization does not duplicate records. Savepoint/transaction failure tests verify rollback of provenance/revision-schema changes and memory corrections. No runtime database was used for these checks.
- **Capture and confirmation:** Cognition validates provider output, persists candidates as pending, and preserves source session/message IDs. Explicit confirmation writes the confirmed memory and changes candidate status atomically in `SQLiteStore`; failure leaves the candidate pending and no confirmed memory. Confirmation assigns `confirmed_candidate`. Rejection leaves no confirmed memory. `/memory add` assigns `manual`; generic API creation defaults to `unknown` (legacy rows without known provenance are `legacy_unknown`).
- **Retrieval:** `SQLiteStore.search_memories()` reads confirmed memory rows only, ranks exact lexical token matches deterministically, resolves ties by update timestamp then ID, suppresses normalized exact duplicates before applying the limit, and returns provenance on each `Memory`. Phrase preference requires an exact consecutive token sequence. Pending/rejected candidates and revision snapshots are in separate tables and are not searchable.
- **Correction and history:** A changed update targets an exact ID, snapshots the previous row and provenance transactionally, preserves the stable ID and original current-row provenance, and records correction origin/source separately. No-op updates create no snapshot; update timestamps advance monotonically. `/memory history <id>` reads chronological snapshots. A ContextBuilder integration test verifies the corrected content can be retrieved while old snapshot content is not injected into the prompt.
- **Deletion:** `/memory delete <id>` requires typing the exact confirmation phrase. SQLite hard deletion cascades to that memory's revision rows. Tests verify unrelated memories survive and a simulated delete failure leaves the current row and history intact.
- **Context and privacy boundary:** ContextBuilder applies memory count/token budgets, caps a retriever that over-returns, and includes only accepted confirmed records. Included memory text is explicitly labeled untrusted contextual data; it cannot replace the system/identity message at the application assembly layer. This is a prompt boundary, not a complete defense against model instruction-following failures. G1 context regression tests remain passing.

### Cross-feature verification

Temporary-database tests cover the complete candidate-to-confirmed-memory-to-retrieval-to-context path; rejection-to-no-retrieval; candidate confirmation rollback; explicit correction-to-revision-to-current-context/history; correction rollback; deletion with history cascade; exact duplicate idempotency and provenance retention; and legacy/repeated migration behavior. Model provider calls are deterministic mocks; no live Ollama inference is part of this verification.

### Exact commands and results

- `python -m pytest tests/test_memory.py tests/test_cognition.py -q` — **70 passed**.
- `python -m pytest tests/test_memory.py tests/test_regression_g1.py tests/test_foundation.py -q` — **57 passed**.
- `python -m pytest -q` — **352 passed, 1 deselected** (the configured optional integration test).
- `python -m compileall -q src/sylphy tests` — **passed**.
- `git diff --check` — **passed**; Git emitted line-ending conversion warnings only.

### G2 closure assessment

The existing G2 requirements are verified for deterministic local storage, explicit candidate review, bounded lexical retrieval, context integration, provenance, correction history, and explicit deletion. G2 is ready to close against this scope. Known limitations remain: semantic conflict/deduplication is not implemented; lexical retrieval misses paraphrases and synonyms; confidence is not calibrated; SQLite data is not encrypted by this application; hard deletion removes revision history; and there is no automatic memory consolidation or decay. These are limits, not silently implemented capabilities.
