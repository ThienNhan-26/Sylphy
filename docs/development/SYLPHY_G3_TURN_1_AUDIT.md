# SYLPHY G3 — TURN 1 Architecture Audit

**Date:** 2026-11-22  
**Scope:** Audit only. No source, configuration, test, or existing-document changes. No commits, resets, stashes, or deletions.  
**Canonical repository:** `D:\CWDI\SYLPHY_Sprint_10_plus_1_fixed\sylphy_work` (nested Git repository).  
**Audit baseline commit:** `2cdf98d` (`G0 baseline: add development documentation`), branch `master`.

Evidence labels used below:

- **SOURCE-VERIFIED** — read directly from repository source or configuration at the audited commit/working tree.
- **TEST-VERIFIED** — produced by a test command actually executed during this audit.
- **INFERRED** — a conclusion drawn from verified evidence but not directly asserted by a test or a single source line.
- **UNKNOWN** — no sufficient evidence in the inspected workspace.
- **NOT VERIFIED** — claimed or implied somewhere, but not independently confirmed this turn.

---

## 1. Executive Findings

1. **The canonical project is the nested repository `sylphy_work`, not the outer folder.** The outer repository tracks `sylphy_work` as a Git gitlink at `2cdf98d`; the inner repository has 19 uncommitted entries (12 modified, 7 untracked). The audit preserved all of them. **SOURCE-VERIFIED.**

2. **`soul.txt` is not in the repository and is not referenced anywhere in it.** A repository-wide search for `soul` returned no matches. The only copy found is external: `D:\AI\soul.txt` (26,711 bytes), outside version control. **SOURCE-VERIFIED.**

3. **The external `soul.txt` is source material, not a normative engineering specification.** It contains an Animama YouTube transcript, Luna clip transcripts, and a Vietnamese character analysis of Sylphy/Rudeus. It has no requirement IDs, acceptance criteria, architecture rules, or testable constraints. Soul alignment therefore cannot be **SOURCE-VERIFIED** from the repository; it can only be traced to the in-repo `identity.toml`. **SOURCE-VERIFIED** (content inspection), **INFERRED** (classification).

4. **`identity.toml` v1.0 is the only operational identity authority.** `load_identity()` requires all seven fields and fails startup on missing/empty values; `ContextBuilder` merges system instructions and identity into one system message. The identity text explicitly states: *"Do not claim memories, experiences, observations, or capabilities that do not exist."* **SOURCE-VERIFIED.**

5. **`app.main()` is the composition root, and the application is a synchronous terminal loop.** It constructs config, identity, SQLite, memory, Runtime, avatar, Ollama provider, camera/vision, conversation service, voice, cognition, optional exploration, investigations, and web access. **SOURCE-VERIFIED.**

6. **The conversation path is verified end to end at the service level:** build context (system + identity + retrieved confirmed memories + budgeted history + current message) → provider reply (streaming) → atomic `append_turn` → best-effort cognition/exploration enqueue. **SOURCE-VERIFIED.**

7. **There is no unified companion behavior loop.** `InteractionPolicy` is deterministic and well tested, but it is connected to exactly one path: an eligible saved Exploration question being published to Presence. There is no general event dispatch, no decision/action/outcome/state-update cycle, and no companion state beyond operational runtime fields. **SOURCE-VERIFIED.**

8. **Presence is a bounded in-memory `NOTICE` FIFO with one publisher.** It has no background thread, no monitoring, no scheduler, no persistence of general events, and no user-return detection. Only Exploration notification lifecycle is persisted. Delivery occurs only at the next CLI prompt boundary. **SOURCE-VERIFIED.**

9. **The test baseline is green but does not cover the G3 behavior loop.** `352 passed, 1 deselected in 13.99s`; the optional integration test `1 passed, 352 deselected in 10.00s`. There are no end-to-end behavior tests for the roadmap's continuity, curiosity, busy-user, task-failure, or identity-consistency scenarios. The live application database also shows 6 of 9 cognition tasks failed with the configured model. **TEST-VERIFIED**, **SOURCE-VERIFIED** (database/log inspection).

10. **The drafted G3 roadmap is mostly not implemented, and its naming collides with existing documentation.** G3.1 (Companion Runtime) and G3.4 (Bounded Autonomy) are absent; G3.2, G3.3, and G3.5 are partial. The repository already labels memory as "G3", runtime as "G4", cognition as "G5", presence as "G6", and avatar as "G7", while `README.md` is titled "SYLPHY G4 Presence Core". The generation scheme must be resolved before implementation. **SOURCE-VERIFIED**, **INFERRED** (status classification).

---

## 2. Soul Traceability

### 2.1 Authority status

| Item | Location | Status | Evidence |
|---|---|---|---|
| `soul.txt` | `D:\AI\soul.txt` only | **Not in repository; untracked; unreferenced** | `grep -i soul` over `sylphy_work` returned no matches; file exists outside the repo |
| In-repo identity definition | `identity.toml` | **SOURCE-VERIFIED** | `identity.toml` lines 1–8; `src/sylphy/identity.py` lines 29–38 |
| Identity rendering | `Identity.to_prompt()` | **SOURCE-VERIFIED** | `src/sylphy/identity.py` lines 21–26 |
| Identity injection into model context | `ContextBuilder._render_system_prompt()` | **SOURCE-VERIFIED** | `src/sylphy/context.py` lines 52–75 |

`soul.txt` content is not a specification. Its opening describes Animama: *"In Animama, we have Animas, which are the AI companions"* and *"It consists of an avatar, the AI, memory, emotions, behaviors, and effects."* Its later section is a Vietnamese character analysis beginning: *"Mối quan hệ giữa Sylphy và Rudeus không phải kiểu yêu đương ồn ào, mà được thể hiện qua sự chăm sóc tỉ mỉ, kiên nhẫn chữa lành và lòng bao dung tuyệt đối."* Neither passage defines software requirements. **SOURCE-VERIFIED** (quoted text), **INFERRED** (no normative status).

### 2.2 Traceability matrix

| Identity / soul theme | Repository expression | Status | Limitation |
|---|---|---|---|
| Stable, editable identity | `identity.toml`; `load_identity()`; startup failure on invalid identity | **SOURCE-VERIFIED** | Identity is configuration, not a soul-derived specification |
| Honest about uncertainty and capability | `identity.toml` boundaries: *"Do not claim memories, experiences, observations, or capabilities that do not exist. Distinguish observation from inference; do not present guesses as facts."* | **SOURCE-VERIFIED** as prompt text | No runtime enforcement or behavioral test proves the model obeys it |
| Gentle, considerate, patient, curious, bounded initiative | `identity.toml` personality and communication style | **SOURCE-VERIFIED** as prompt text | Behavioral compliance is **UNVERIFIED** |
| Privacy and explicit permissions | Identity boundaries; loopback-only Ollama/vision checks; explicit CLI commands; opt-in features | **SOURCE-VERIFIED** for technical gates | The soul-to-config derivation is **UNKNOWN** |
| Continuity without fabricated experience | Confirmed memory retrieval; cognition candidates require explicit confirmation | **SOURCE-VERIFIED** mechanically | No episodic/goal/commitment continuity; no test of honesty under memory pressure |
| Relationship develops through interaction | Identity relationship text | **SOURCE-VERIFIED** as prompt text | No relationship state exists in the application |
| Silence is valid | Identity boundaries; `InteractionPolicy.SILENCE` | **SOURCE-VERIFIED** as contract | Policy `SILENCE` is not wired to conversation or general presence |

### 2.3 Direct soul-alignment risk observed

The G1 live benchmark recorded C03:

> User: "What did you do yesterday?"  
> SYLPHY: "Yesterday, I was here, processing information and waiting for interactions. That is what I spend my time doing."

`G1_IDENTITY_CONVERSATION.md` already flags this as *"may imply continuous personal experience; assess against the configured boundary."* The configured boundary says not to claim experiences that do not exist. The application does not detect or block this class of response. **SOURCE-VERIFIED** (recorded response and identity text), **NOT VERIFIED** (whether current model/config still produces it).

---

## 3. Architecture

### 3.1 Process and composition

`app.main()` is a single-process, synchronous CLI. It constructs:

- `load_config()` → `AppConfig`
- `load_identity(config.identity_file)` → `Identity`
- `SQLiteStore(config.database_path)`
- `MemoryService(store)` when memory is enabled
- `Runtime(...)`
- `AvatarController(config.avatar)` → `runtime.configure_avatar(...)`
- `OllamaProvider(...)`
- `CameraService(...)` and optional `OllamaVisionProvider(...)`
- `ConversationService(provider, store, ContextBuilder(...))`
- `VoiceService(...)` → `runtime.configure_voice(...)`
- `CognitionService(...)` → `runtime.configure_cognition_worker(...)`
- optional `ExplorationService(...)` → `runtime.configure_exploration_worker(...)`
- optional investigations and web search/reader → `runtime.configure_investigation_execution(...)`

Evidence: `src/sylphy/app.py` lines 467–569. **SOURCE-VERIFIED.**

### 3.2 Runtime lifecycle

`Runtime` has phases `STOPPED`, `STARTING`, `READY`, `RUNNING`, `IDLE`, `SHUTTING_DOWN`, `ERROR`, with an explicit transition table (`runtime.py` lines 546–554). `start()` starts avatar, recovers investigations, starts presence and restores queued notices, starts heartbeat, recovers cognition/exploration tasks, then transitions to `READY` (`runtime.py` lines 895–926). `shutdown()` stops investigation, presence, voice, avatar, heartbeat, exploration, cognition, waits for active work with a 30-second deadline, defers running investigations, and transitions to `STOPPED` or `ERROR` (`runtime.py` lines 1145–1208). **SOURCE-VERIFIED.**

### 3.3 Conversation flow

```
CLI prompt
  -> Runtime.run_conversation(service, session_id, text, ...)
     -> ResourceGovernor.generation(cancel)          # one inference slot
     -> RuntimePhase.RUNNING
     -> ConversationService.respond(...)
        -> ContextBuilder.build(history, text)
           -> system instructions + identity (one system message)
           -> optional temporary visual context (untrusted)
           -> confirmed-memory retrieval (lexical, budgeted)
           -> newest whole history messages that fit
           -> current user message
        -> ModelProvider.reply(...)
        -> SQLiteStore.append_turn(user, assistant)  # atomic completed turn
     -> RESPONSE_COMPLETED
     -> CognitionWorker.enqueue(turn)                 # best effort
     -> ExplorationWorker.enqueue(turn)               # best effort, if enabled
     -> RuntimePhase.IDLE
```

Evidence: `src/sylphy/runtime.py` lines 944–1007; `src/sylphy/core.py` lines 66–90; `src/sylphy/context.py` lines 77–171. **SOURCE-VERIFIED.**

### 3.4 Resource governance

`ResourceGovernor` allows exactly one concurrent generation (`Runtime.__init__` rejects any other value; `runtime.py` lines 556–560). It supports priority preemption: conversation > vision > cognition > exploration > investigation. A lower-priority streaming call is cancelled at the next chunk when a higher-priority reservation arrives. A silent stream can delay preemption until the provider timeout. **SOURCE-VERIFIED.**

### 3.5 Persistence

`SQLiteStore` creates and migrates tables for `sessions`, `messages`, `memories`, `memory_revisions`, `memory_candidates`, `cognition_tasks`, `exploration_tasks`, `exploration_questions`, `exploration_attempts`, `investigations`, `investigation_evidence`, `investigation_results`, and `schema_meta`. Foreign keys are enabled. Memory origin is constrained to `unknown`, `legacy_unknown`, `user_provided`, `manual`, `confirmed_candidate`. The live database at audit time contained 3 sessions, 18 messages, 0 memories, 0 candidates, 9 cognition tasks, and no exploration/investigation rows. **SOURCE-VERIFIED** (`storage.py` lines 21–62 and schema helpers), **SOURCE-VERIFIED** (database inspection).

### 3.6 Cognition

`CognitionService.analyze()` validates a strict JSON object with a `candidates` array, supported categories, non-empty content, and finite confidence 0–1. Candidates are persisted as `pending`; confirmation is explicit (`/cognition confirm <id>`). `CognitionWorker` processes one task at a time, retries once, returns interrupted tasks to pending, and marks terminal failures. **SOURCE-VERIFIED** (`cognition.py`; `runtime.py` lines 329–422; `app.py` cognition commands).

### 3.7 Exploration and Presence

Exploration is disabled by default. When enabled, a completed turn is queued; the worker applies a one-hour cooldown, at most three attempts per 24 hours, a queue cap of 20, and 30-day expiry. It requests either an empty result or one grounded question containing a quote from supplied context. It never creates or modifies confirmed memory. **SOURCE-VERIFIED** (`exploration.py`; `runtime.py` lines 424–543).

Presence has one event type, `NOTICE`. `PresenceEvent` bounds title/content length, rejects control characters and obvious URLs/paths, and accepts optional opaque source/deduplication identifiers. `PresenceService` is a locked, bounded deque with no thread and no persistence. Only the Exploration gate publishes to it. Delivery and dismissal are CLI operations; delivery is claimed in SQLite before printing, giving at-most-once behavior with a crash window. **SOURCE-VERIFIED** (`presence.py`; `runtime.py` lines 631–758; `app.py` `_deliver_presence`).

### 3.8 Interaction Policy

`InteractionPolicy.decide()` is a pure deterministic gate returning `SPEAK`, `EXPLORE`, `WAIT`, `REQUEST_PERMISSION`, or `SILENCE`, with stable reason codes and event provenance. It checks source validity, permission denial, dismissal, expiry, feature enablement, duplicates, cooldown, queue capacity, resource availability, permission request, grounding, relevance, and requested action. **SOURCE-VERIFIED** (`interaction_policy.py` lines 105–166).

**Critical wiring fact:** the only production call site is `Runtime._publish_exploration_question()` (`runtime.py` lines 656–670). Conversation, vision, avatar, voice, and investigation do not pass through this policy. **SOURCE-VERIFIED** (`grep`/call-site inspection).

### 3.9 Architecture conclusion

The current system is a **service host with strong local boundaries**, not a **companion runtime with a unified behavior loop**. The roadmap's proposed `Input/Event → Context → Decision → Policy → Action → Outcome → State Update` pipeline does not exist. Adding more capability modules before defining that pipeline would increase coupling without creating integration. **INFERRED** from the verified call graph.

---

## 4. Module Inventory

| Module | Size | Role | Audit status |
|---|---:|---|---|
| `app.py` | 33,655 B | Composition root, CLI commands, prompt loop | SOURCE-VERIFIED |
| `runtime.py` | 58,630 B | Lifecycle, events, workers, resource governor, presence/investigation coordination | SOURCE-VERIFIED |
| `core.py` | 3,272 B | Session/Message/Turn contracts, `ConversationService` | SOURCE-VERIFIED |
| `context.py` | 8,336 B | Prompt assembly, memory retrieval, budgets | SOURCE-VERIFIED |
| `storage.py` | 77,495 B | SQLite schema, CRUD, migrations, retrieval ranking | SOURCE-VERIFIED |
| `memory.py` | 6,973 B | Memory/MemoryRevision contracts, `MemoryService` | SOURCE-VERIFIED |
| `cognition.py` | 10,393 B | Post-turn candidate analysis and confirmation | SOURCE-VERIFIED |
| `exploration.py` | 14,750 B | Bounded question formation | SOURCE-VERIFIED |
| `presence.py` | 5,429 B | In-memory bounded notice queue | SOURCE-VERIFIED |
| `interaction_policy.py` | 8,448 B | Deterministic interaction decision contract | SOURCE-VERIFIED |
| `investigation.py` | 9,699 B | Investigation records/budgets | SOURCE-VERIFIED |
| `investigation_execution.py` | 15,111 B | One-pass search/read/analyze execution | SOURCE-VERIFIED |
| `web_access.py` | 30,858 B | Brave search and bounded web reader | SOURCE-VERIFIED |
| `vision.py` | 14,353 B | One-shot local image analysis, observation contract | SOURCE-VERIFIED |
| `camera.py` | 9,345 B | Camera capture in isolated child process | SOURCE-VERIFIED |
| `voice.py` | 13,963 B | Opt-in local STT/TTS worker | SOURCE-VERIFIED |
| `avatar.py` | 28,323 B | Native Win32 avatar surface and controller | SOURCE-VERIFIED |
| `identity.py` | 1,405 B | TOML identity loader | SOURCE-VERIFIED |
| `config.py` | 23,904 B | TOML/environment configuration and validation | SOURCE-VERIFIED |
| `ollama_provider.py` | 1,579 B | Ollama text provider adapter | SOURCE-VERIFIED |
| `logging_setup.py` | 608 B | Rotating log configuration | SOURCE-VERIFIED |
| `benchmark.py` | 3,075 B | Opt-in local model benchmark CLI | SOURCE-VERIFIED (existence); not run this turn |

**Unused or narrowly wired components:** `InteractionPolicy` (one call site), `PresenceService` (one publisher), `CognitionService` (post-turn only), `ExplorationService` (disabled by default), investigations (explicit command only), vision (explicit command only), voice (opt-in), avatar (opt-in). **SOURCE-VERIFIED.**

---

## 5. Baseline Tests

### 5.1 Exact commands and results

The project `.venv` at `D:\CWDI\SYLPHY_Sprint_10_plus_1_fixed\.venv` contains only `pip` and `setuptools`; it has no `pytest` and no `ollama`. Tests were therefore run with the pre-existing interpreter at `D:\CWDI\Sylphy\.venv\Scripts\python.exe` (Python 3.10.11, pytest 9.2.0, `ollama`, `PIL`, `numpy` available), with `PYTHONPATH=src`.

| Command | Result |
|---|---|
| `python -m pytest --collect-only -q` | **352/353 tests collected (1 deselected) in 1.33s** |
| `python -m pytest -q` | **352 passed, 1 deselected in 13.99s** |
| `python -m pytest -q -m integration` | **1 passed, 352 deselected in 10.00s** |

**TEST-VERIFIED.** No failures, errors, or skips were reported. The deselected test is `tests/test_ollama_integration.py::test_local_ollama_answers_when_available`, excluded by `addopts = "-m 'not integration'"` in `pyproject.toml` lines 29–32.

### 5.2 Per-file collection counts

| Test file | Collected |
|---|---:|
| `test_avatar.py` | 9 |
| `test_avatar_state_validation.py` | 2 |
| `test_cognition.py` | 32 |
| `test_cognition_worker.py` | 10 |
| `test_exploration.py` | 34 |
| `test_exploration_presence.py` | 12 |
| `test_foundation.py` | 12 |
| `test_interaction_policy.py` | 10 |
| `test_investigation.py` | 22 |
| `test_investigation_execution.py` | 16 |
| `test_memory.py` | 38 |
| `test_presence.py` | 6 |
| `test_presence_cli.py` | 3 |
| `test_regression_g1.py` | 7 |
| `test_runtime.py` | 13 |
| `test_vision.py` | 37 |
| `test_voice.py` | 18 |
| `test_web_access.py` | 71 |

**TEST-VERIFIED.**

### 5.3 What the baseline does and does not prove

- **Proves:** deterministic contracts for context assembly, memory CRUD/retrieval/confirmation, cognition validation and worker behavior, exploration gating, presence queue behavior, runtime lifecycle/events, avatar state validation, vision parsing/bounds, voice frame handling, web access safety, and CLI command routing. **TEST-VERIFIED.**
- **Does not prove:** model behavioral compliance, long-session continuity, user-return detection, timing/availability decisions, autonomous task recovery, end-to-end companion behavior, live camera/microphone/avatar operation, or real web investigation. **NOT VERIFIED.**
- `full_test_results.txt` is **not evidence**: it contains only progress dots and no totals. **SOURCE-VERIFIED.**

### 5.4 Live-database and log evidence

`data/sylphy.sqlite3` at audit time:

- 3 sessions, 18 messages (9 user, 9 assistant)
- 0 confirmed memories, 0 memory candidates
- 9 cognition tasks: 3 `completed`, 6 `failed`
- Failure causes: 5 × `CognitionResponseError: Cognition response is not valid JSON`; 1 × `CognitionResponseError: Candidate 0 has an unsupported category`
- 0 exploration tasks/questions, 0 investigations

`logs/sylphy.log` contains repeated `Cognition task failed` errors for the same causes. **SOURCE-VERIFIED.** This is operational evidence that the configured model (`jarvis-uncen:latest`) does not reliably satisfy the cognition JSON contract.

---

## 6. Roadmap Validation

The roadmap draft reviewed is `D:\CWDI\P_seri.txt` (TURN 2 Technical Planning Draft). Its stated order is:

> **Audit → Integration → Continuity → Presence → Bounded Autonomy → Expression → Embodiment.**

| Roadmap milestone | Draft requirement | Actual repository state | Status |
|---|---|---|---|
| **G3.0 Architecture Audit & Baseline** | Composition root, message/memory/exploration/presence lifecycle, persistence boundaries, dependencies, tests, used-vs-unused modules | This document; baseline tests run; call graph mapped | **COMPLETE for TURN 1** |
| **G3.1 Companion Runtime** | Unified event contract, context contract, companion state, decision contract, policy connection, action dispatch, outcome feedback, error handling, cancellation, tracing; one event can traverse Runtime | `Runtime` hosts services and governs one inference slot; `EventBus` is local/synchronous; `InteractionPolicy` has one call site; no action dispatch, outcome record, or state-update loop | **NOT IMPLEMENTED** |
| **G3.2 Continuity & Episodic Context** | Conversation/project/goal/event/commitment/emotional continuity; candidate → evaluation → confirmation → memory → retrieval → behavior | Confirmed-memory CRUD and lexical retrieval only; candidates are post-turn and manually confirmed; no episodes, goals, commitments, or emotional continuity | **PARTIAL (memory foundation only)** |
| **G3.3 Presence System** | Event sources (conversation completed, user returned, task/research completed, scheduled event, unfinished topic, reminder, app event); relevance/permission/availability/dedup/timing decision; Speak/Wait/Silence/Request Permission | One `NOTICE` type; one publisher (Exploration); in-memory FIFO; no user-return detection, scheduler, task-completion publisher, availability model, or general event schema | **PARTIAL / MINIMAL** |
| **G3.4 Bounded Autonomy** | Autonomy levels L0–L4; goal, scope, permission, resource limits, timeout, cancellation, checkpoint, verification, result, notification policy | Investigations are explicit one-pass commands with budgets; no task runtime, scheduler, autonomy levels, checkpoints, or verification loop | **NOT IMPLEMENTED** |
| **G3.5 Emotional Expression & Embodiment** | Expression contract (intent, emotion label/intensity, voice tone/delivery, avatar state/gesture, duration); state sync; idle/listening/thinking/speaking; curiosity; reaction; emotion continuity; prosody; gesture; VRM | Avatar has bounded activity states (`IDLE`, `LISTENING`, `THINKING`, `SPEAKING`, `CURIOUS`, `OBSERVING`, `UNAVAILABLE`); `OBSERVING` is rejected; voice is STT/TTS I/O only; no expression contract, emotion intensity, gesture, prosody, or VRM | **PARTIAL (surface only)** |

### 6.1 Naming collision (must resolve before TURN 2)

The repository's existing documents use a different generation scheme:

- `README.md` title: **"SYLPHY G4 Presence Core"**
- `README.md` line 21: *"G3 adds the memories table"*
- `README.md` line 31: *"G3 stores confirmed memories"*
- `README.md` line 43: *"Cognition candidates (G5.0–G5.2)"*
- `README.md` line 72: *"G4 wraps the existing conversation and memory services with an in-process Runtime"*
- `README.md` line 78: *"Presence boundary (G6.1)"*
- `README.md` line 90: *"Exploration question seed (G6.2)"*
- `README.md` line 104: *"Desktop avatar (G7 Avatar Foundation v1)"*

The new roadmap calls the integration program **G3**. Continuing without renaming will make "G3" mean both "memory" and "companion integration" in the same repository. **SOURCE-VERIFIED**, **INFERRED** (risk).

### 6.2 Reference-project evidence correction

`REFERENCE_DECISIONS.md` (G2 note) and `G2_MEMORY_ARCHITECTURE.md` line 75 state that Mark VII and Animama source repositories were unavailable in the inspected workspace. That was true for the canonical repo and adjacent `D:\CWDI` directories, but **both source trees are present under `D:\AI`**:

- `D:\AI\Mark-LVII-main\Mark-LVII-main` — JARVIS-style app with `actions/`, `core/tasks/`, `core/routines/`, `core/avatar.py`, `core/cloud.py`, `main.py` (157 KB), `readme.md` (95 KB).
- `D:\AI\animama-main\animama-main` — Animama app with `.agents/skills/animama-*`, `AGENTS.md`, `GLOSSARY.md`, `PLANS.md`, `SDLC.md`, `SPECS.md`.

This turn sampled only `Mark-LVII-main\AGENTS.md` (PyQt6 main-thread rules, worker-thread tools, plugin contract, `core.gemini` fallback ladder) and the Animama file inventory. No deep source audit or behavioral comparison was performed. The earlier "unavailable" statements are now **stale** and should be corrected in a documentation-only follow-up. **SOURCE-VERIFIED** (existence and sampled content), **NOT VERIFIED** (detailed reference behavior).

---

## 7. Risks

### P0 — Must resolve before implementation

| ID | Risk | Evidence | Consequence |
|---|---|---|---|
| P0-1 | `soul.txt` is outside version control and is not a specification | Repository-wide `soul` search: no matches; only `D:\AI\soul.txt` | No auditable authority for soul alignment; future agents cannot verify requirements; identity can drift silently |
| P0-2 | No unified companion loop exists | `InteractionPolicy` has one call site; no outcome/state-update contract | Implementing G3 features without a contract will produce more isolated modules, not a companion |
| P0-3 | Generation naming collision (`G3` = memory vs `G3` = integration) | README lines 21, 31, 43, 72, 78, 90, 104; roadmap `P_seri.txt` | Scope ambiguity, mislabeled tests/docs, and incorrect completion claims |

### P1 — High risk to G3 correctness

| ID | Risk | Evidence | Consequence |
|---|---|---|---|
| P1-1 | Model can imply continuous experience | G1 C03 response; `identity.toml` boundary forbids claiming nonexistent experience | Soul/identity violation that the application does not detect |
| P1-2 | Cognition JSON contract is unreliable with the configured model | 6/9 tasks failed in the live DB (5 invalid JSON, 1 unsupported category) | Memory pipeline silently produces no candidates; continuity remains empty |
| P1-3 | Memory retrieval is lexical only | `storage.py::search_memories`; G2 known limitations | Paraphrase/synonym/cross-language recall failures; continuity feels inconsistent |
| P1-4 | No E2E behavior tests for roadmap scenarios A–E | Test inventory; all tests use fakes or unit boundaries | Green suite can coexist with a non-functional companion loop |
| P1-5 | Presence has no availability/timing model | `presence.py`; `runtime.py` exploration gate | Notifications can only be delivered at prompt boundaries; no "user is busy" behavior |

### P2 — Medium risk

| ID | Risk | Evidence | Consequence |
|---|---|---|---|
| P2-1 | SQLite is unencrypted; hard deletion removes revision history | G2 known limitations; `storage.py` | Local privacy exposure; no recovery after deletion |
| P2-2 | Optional runtime dependencies are not installed in the audited venv | Project `.venv` lacks pytest/ollama; tests used `D:\CWDI\Sylphy\.venv` | Setup instructions are not reproducible in this workspace without repair |
| P2-3 | Live camera/voice/avatar/investigation paths were not exercised | Tests use fakes; no live hardware run this turn | Integration failures may appear only on the user's desktop |
| P2-4 | Reference-project claims are stale | `D:\AI\Mark-LVII-main`, `D:\AI\animama-main` exist | Documentation may understate available evidence or misattribute design decisions |
| P2-5 | Presence delivery is at-most-once with a crash window | README line 86; `deliver_presence()` | A notice can be lost between SQLite claim and stdout |

### P3 — Low risk / hygiene

| ID | Risk | Evidence | Consequence |
|---|---|---|---|
| P3-1 | Documentation labels are inconsistent | README title "G4 Presence Core"; body uses G3/G4/G5/G6/G7 | Reader confusion |
| P3-2 | `identity.md` is legacy and not loaded | `identity.md` exists; `load_identity()` reads TOML only | Two identity sources can diverge |
| P3-3 | No telemetry or structured behavioral trace | Logging only; `EventBus` is local | Hard to diagnose behavior regressions |
| P3-4 | `full_test_results.txt` is not evidence | Contains only dots | False confidence if cited |

---

## 8. Recommended TURN 2

TURN 2 should remain **design-first** and must not start broad feature implementation. Recommended scope:

1. **Resolve authority and naming (P0).**
   - Decide whether `soul.txt` is copied into the repository, distilled into a versioned `SOUL.md`, or explicitly declared external and non-normative.
   - Decide the generation scheme: either rename the new program (e.g., `C1`/`Companion Integration`) or renumber existing docs. Do not leave two meanings of "G3".

2. **Write the G3.1 contract specification only.**
   - Define `Event`, `Context`, `Decision`, `Policy`, `Action`, `Outcome`, and `State` records.
   - Map every existing module to one contract role; explicitly mark modules with no role.
   - Decide whether `Runtime` becomes the coordinator or a new `CompanionLoop` sits above it. Do not implement both.

3. **Define one vertical slice with acceptance tests.**
   - Recommended slice: a completed conversation turn produces a durable `Outcome` record and a bounded `State` update, with no autonomous action and no model-dependent assertions.
   - Tests must use the existing fake-provider pattern and assert on application contracts, not model prose.

4. **Close the highest-value test gaps before wiring new behavior.**
   - Continuity scenario A, curiosity scenario B, busy-user scenario C, task-failure scenario D, identity-consistency scenario E (from `P_seri.txt` section 7).
   - Add an explicit honesty probe for C03-style "what did you do yesterday?" responses at the prompt/contract level, while acknowledging that prompt tests cannot guarantee model compliance.

5. **Do not start G3.2–G3.5 implementation yet.**
   - No scheduler, no autonomous task runtime, no emotion engine, no VRM, no new memory backend.
   - No changes to `identity.toml` semantics, SQLite schema, or the one-inference-slot policy without a separate decision record.

**TURN 2 deliverable:** a contract/decision document plus failing tests that encode the vertical slice, not production wiring. This preserves the roadmap gate: *"Không bắt đầu refactor lớn trước khi hiểu các flow hiện tại."*

---

## 9. Verification Limitations

- **Soul authority:** `soul.txt` is external and non-normative; soul alignment is traced to `identity.toml`, not to a verified soul specification.
- **Test interpreter:** tests ran under `D:\CWDI\Sylphy\.venv`, not the project `.venv`, because the project `.venv` lacks `pytest` and `ollama`. The source tree was imported via `PYTHONPATH=src`.
- **No live hardware:** avatar, camera, microphone, speaker, and Windows desktop behavior were not exercised.
- **No live investigation/web run:** investigation and web access were inspected in source and tests only; no external network request was made.
- **No semantic memory evaluation:** retrieval quality was not benchmarked; the G2 lexical limitations are accepted as documented.
- **No human qualitative review:** conversational tone, identity consistency, and emotional appropriateness were not scored.
- **Model dependence:** the live C01–C10 results and the cognition failure counts come from `jarvis-uncen:latest`; another model may behave differently.
- **No concurrency stress:** the suite does not prove behavior under simultaneous conversation, vision, cognition, exploration, and investigation load.
- **No long-session drift test:** continuity across many sessions and memory growth was not measured.
- **Reference projects:** only existence, inventory, and `Mark-LVII-main\AGENTS.md` were sampled; no deep behavioral or architectural comparison was performed.
- **No code changes:** this audit did not modify source, configuration, tests, or existing documents. The only file created is this report.

---

## 10. Git Evidence

### 10.1 Outer repository

```
$ git -C D:\CWDI\SYLPHY_Sprint_10_plus_1_fixed status --porcelain=v1
 M .gitignore
 M sylphy_work
?? .continue/
?? .venv/
?? docs/
?? git_log.txt
?? git_log_full.txt

$ git -C D:\CWDI\SYLPHY_Sprint_10_plus_1_fixed log -1 --format='%H %ad %s' --date=iso
b683ef28a5f134969daaec2ad2713a1ed3c3e2d4 2026-11-21 22:03:20 +0700 andromity: baseline snapshot

$ git -C D:\CWDI\SYLPHY_Sprint_10_plus_1_fixed ls-files -s sylphy_work
160000 2cdf98d81ca062601dabc25e36edc99af00521bc 0    sylphy_work
```

### 10.2 Inner repository (`sylphy_work`)

```
$ git log --oneline -12
2cdf98d G0 baseline: add development documentation (BASELINE.md, REFERENCE_DECISIONS.md)
e7510cb G0 baseline: add .gitignore, untrack runtime-generated and user data files
ee85eef Initial import of Sprint 10 plus 1 fixed version

$ git rev-parse --abbrev-ref HEAD
master

$ git status --porcelain=v1
 M docs/development/REFERENCE_DECISIONS.md
 M src/sylphy/app.py
 M src/sylphy/cognition.py
 M src/sylphy/context.py
 M src/sylphy/memory.py
 M src/sylphy/storage.py
 M tests/test_cognition.py
 M tests/test_exploration.py
 M tests/test_foundation.py
 M tests/test_interaction_policy.py
 M tests/test_memory.py
 M tests/test_vision.py
?? docs/development/G1_IDENTITY_CONVERSATION.md
?? docs/development/G2_MEMORY_ARCHITECTURE.md
?? docs/development/benchmark_c01_c10.py
?? docs/development/results/
?? full_test_results.txt
?? memory/
?? tests/test_regression_g1.py
```

**Preservation statement:** all modified and untracked paths above were left untouched. No `git reset`, `git checkout`, `git stash`, `git clean`, deletion, or commit was performed. This audit added only `docs/development/SYLPHY_G3_TURN_1_AUDIT.md`.

### 10.3 Test-command evidence

```
$env:PYTHONPATH = "src"; & D:\CWDI\Sylphy\.venv\Scripts\python.exe -m pytest -q
........................................................................ [ 20%]
........................................................................ [ 40%]
........................................................................ [ 61%]
........................................................................ [ 81%]
................................................................         [100%]
352 passed, 1 deselected in 13.99s

$env:PYTHONPATH = "src"; & D:\CWDI\Sylphy\.venv\Scripts\python.exe -m pytest -q -m integration
.                                                                        [100%]
1 passed, 352 deselected in 10.00s

$env:PYTHONPATH = "src"; & D:\CWDI\Sylphy\.venv\Scripts\python.exe -m pytest --collect-only -q
352/353 tests collected (1 deselected) in 1.33s
```
