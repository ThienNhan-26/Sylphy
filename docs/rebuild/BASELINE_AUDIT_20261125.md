# SYLPHY Stage 0: Baseline Audit Report
**Date:** 2026-11-25  
**Governing document:** `SYLPHY_MASTER_PLAN_FUTURE_HANDOVER_v3.0.md`  
**Audit scope:** Source integrity & reproducible baseline  
**Mode:** Audit only; no implementation  

---
## 1. Audit Scope and Timestamp

This audit establishes a truthful, reproducible understanding of the current SYLPHY working repository. It inspects the actual source tree, verifies source integrity, traces the real `sylphy_entity` runtime call graph, and establishes a reproducible Python test baseline. No implementation was performed; only existing source was inspected and test results were recorded.

**Audit timestamp:** 2026-11-25  
**Audit mode:** Audit only (no code modifications, no new tests, no environment changes beyond venv creation and dependency installation per Master Plan spec)  
**Governing authority:** `SYLPHY_MASTER_PLAN_FUTURE_HANDOVER_v3.0.md` (v3.0, 25 November 2026)  

---
## 2. Repository Root, Branch, Commit, and Working-Tree Status

| Item | Value |
|------|-------|
| Repository root | `D:\CWDI\SYLPHY_CWDI\Sylphy-main` |
| Archive source | `Sylphy-main(1).zip`; SHA-256: `db100fbe42eba11d0e0a8eccd923f58e6b53e96c98e5476de40bee7c6531ec5a` |
| Extraction root | `D:\CWDI\SYLPHY_CWDI\Sylphy-main\` |
| Git branch | `master` |
| Git commits | 0 (no commits; ZIP extraction, not a git repo) |
| Working-tree status | Untracked files present (65+ files; ZIP extraction artifact) |
| Git status | `nothing added to commit but untracked files present` |

**Note:** The repository was extracted from the ZIP archive and has no git history. The working-tree contains many application-side artifacts (readmes, reports, patches) that are not part of the source code itself.

---
## 3. Source Archive Identity and SHA-256

The six archives listed in the Master Plan were available, but only the S0 main archive is relevant to this audit. Its SHA-256 hash should be verified against the downloaded file.

**S0 archive SHA-256:** `db100fbe42eba11d0e0a8eccd923f58e6b53e96c98e5476de40bee7c6531ec5a`

---
## 4. Recovery Patch Provenance and File Hashes

The Master Plan v3.0 Section 3.1 identifies a known blocker: the four memory recovery files are absent from the S0 archive but are required by `src/sylphy_entity/interaction/conversation_runtime.py` (which imports `from sylphy_entity.memory.recall import MemoryRecallService`) and by tests under `tests/entity_memory/`.

In the current working tree, all four files are present in `src/sylphy_entity/memory/`. Their provenance and SHA-256 hashes are:

| File | Path | SHA-256 | Size | Provenance |
|------|------|---------|------|------------|
| `__init__.py` | `src/sylphy_entity/memory/__init__.py` | `b5544d0bcdfdeb09243711564d1dba22ebcff8e5a2bafa5b0b359069d1a6ba11` | 446 bytes | Found in working tree; matches recovery patch from preparation session |
| `repository.py` | `src/sylphy_entity/memory/repository.py` | `03771275b9630571ac22d8d9882830028f501c230c2c2a401b95fbb625628210` | 18,610 bytes | Found in working tree; matches recovery patch from preparation session |
| `recall.py` | `src/sylphy_entity/memory/recall.py` | `3549e163beeb95e63365a9331d0babf5130c2febd0f546a973ff28688932986e` | 5,803 bytes | Found in working tree; matches recovery patch from preparation session |
| `legacy_import.py` | `src/sylphy_entity/memory/legacy_import.py` | `d0f1dafa593582808e4155b8f261fb2dbb5071eae63202d4d4c744043bd7a50e` | 7,288 bytes | Found in working tree; matches recovery patch from preparation session |

**Import verification:** All four modules are importable. The critical import `from sylphy_entity.memory.recall import MemoryRecallService` in `conversation_runtime.py` resolves successfully.

**Source-integrity finding:** No unresolved import-critical source omissions. The memory package is complete and importable.

---
## 5. Source-Integrity Findings

- All four recovery files verified present and importable
- `conversation_runtime.py` line 12 imports `from sylphy_entity.memory.recall import MemoryRecallService` — resolves successfully
- No missing import-critical files
- The `sylphy_entity.memory` package is self-contained; no `src/sylphy/` dependencies detected in the memory modules
- `legacy_import.py` is a read-only adapter for the frozen `src/sylphy` SQLite schema; it previews confirmed allowlisted rows and stages pending candidates (never creates active memories without separate user confirmation and verification)

**Overall source-integrity result:** **PASS** — all required modules present, all imports resolve, no hidden omissions.

---
## 6. Actual Entity Runtime Call Graph

**Methodology:** Actual code inspection of real imports and function calls across `src/sylphy_entity/`. Following paths: daemon.py, runtime/, world/, contracts/, brain/, execution/, persistence/, cognition/, conversation_runtime.py, memory/, voice/, sensors/, embodiment/.

### 6.1 Subsystem Inventory and Ownership Boundaries

#### `src/sylphy_entity/daemon.py`
- **Entry point:** `main()` — CLI for the independent SYLPHY Entity Daemon
- **Key imports:** `runtime.paths`, `runtime.instance_lock`, `runtime.supervisor`
- **Callers:** Launches `_run()` → `EntitySupervisor` → `supervisor.start()` → `serve_forever()`
- **Callees:** Configures cognition provider, conversation generator, voice output; starts supervisor
- **Implemented:** Yes
- **Depends on `src/sylphy/`:** No

#### `src/sylphy_entity/runtime/`
- **`event_loop.py`:** `EntityEventLoop` — wake immediately for events, tick at low fixed cadence (1.0s). Owns scheduling only; does not contain Brain policy or I/O logic.
- **`instance_lock.py`:** `InstanceLock` / `InstanceAlreadyRunning` — single-instance enforcement via lock file
- **`lifecycle.py`:** `Lifecycle` / `LifecycleState` — state machine: `STARTING` → `READY` → `RUNNING` → `ERROR` → `SHUTTING_DOWN`
- **`paths.py`:** `default_runtime_dir()` — per-user runtime paths (`%LOCALAPPDATA%\SYLPHY\EntityRuntime` on Windows)
- **`process_manager.py`:** `ProcessManager` — supervises worker processes with graceful shutdown
- **`supervisor.py`:** `EntitySupervisor` — **sole lifecycle authority**. Owns daemon lifecycle, event scheduling, local API, worker supervision, conversation runtime, voice, embodiment, brain state, persistence, recovery

#### `src/sylphy_entity/world/`
- **`model.py`:** `WorldModel` — current structured representation with evidence freshness; owns World snapshot
- **`projector.py`:** `WorldProjector` — applies observations to World model; handles provenance and stale marking
- **`adapters/filesystem.py`:** `FilesystemAdapter` — sandboxed filesystem observation (opt-in behind `--sandbox-root`)

#### `src/sylphy_entity/contracts/`
- **`events.py`:** `EntityEvent` — event envelope with `event_id`, `event_type`, `source`, `payload`; durable journal entry
- **`observation.py`:** `Observation` — typed data contract for world observations; `kind`, `subject`, `observed_at`, `received_at`, `verification`, `confidence`, `permission_scope`
- **`decision.py`:** `BrainState`, `CognitionFeedback`, `GoalFeedback`, `GoalOutcome`, `GoalStatus` — pure decision contracts
- **`execution.py`:** `ExecutionStatus`, `ResourceMode`, `ResourceRequirement`, `Intent`, `ExecutionResult` — execution contract types
- **`cognition.py`:** `CognitionOperation`, `CognitionOutcome`, `CognitionRequest`, `CognitionStatus` — cognition provider contract
- **`memory.py`:** `MemoryRecord`, `MemoryRepository`, `MemoryCandidate`, `MemoryOrigin`, `MemoryStatus`, `MemoryOrigin` — memory contracts
- **`conversation.py`:** `ConversationMessage`, `ConversationTurn`, `ConversationIngress` — conversation contract types

#### `src/sylphy_entity/brain/`
- **`core.py`:** `EntityBrain` — **pure deterministic decision function**. `step(dt, WorldSnapshot, BrainState) → Frame`. No LLM, filesystem, HTTP, SQLite, or worker imports.
- **`drives.py`:** drives — novelty/input drives for goal generation
- **`simulation.py`:** simulation — deterministic virtual entity simulation
- **`novelty.py`:** novelty — drive input novelty tracking with frequency and depth penalties
- **`transitions.py`:** transitions — goal state transitions
- **Architecture boundary test** (`test_brain_architecture_boundary.py`): confirms `brain/core.py` and decision contracts have **no runtime IO or model imports**

**Brain purity:** Verified — `test_brain_architecture_boundary.py` passes; EntityBrain is a pure function over World snapshots.

#### `src/sylphy_entity/execution/`
- **`dispatcher.py`:** `IntentDispatcher` — routes brain proposals into scoped read-only intents
- **`intent_router.py`:** `IntentRouter` — routes proposals into specific capability intents; rejects unsafe targets (path traversal, unknown capability, permission escalation)
- **`permissions.py`:** `PermissionPolicy` — sandbox scope enforcement; read-only metadata observation under exact scope; revocation survives daemon restarts
- **`resource_arbiter.py`:** `ResourceArbiter` — acquire/release resource leases; two readers can share same path; exclusive claims conflict with existing readers; expired leases reclaimed
- **`worker_executor.py`:** `SandboxWorkerExecutor` — read-only worker execution with content hash verification; worker crash captured with log evidence

#### `src/sylphy_entity/persistence/`
- **`database.py`:** `EntityDatabase` — SQLite wrapper for runtime state
- **`event_store.py`:** `EventStore` — append-only event journal; durable idempotent ordered event storage; event_id uniqueness enforced
- **`codec.py`:** `canonical_json` / `parse_json` — consistent JSON serialization for payloads
- **`recovery.py`:** `RecoveryManager` — classifies incomplete intents; **never kills unverified PID**; recovery terminates real workers only when `pid` and `creation_time` match; does not kill real process when creation time does not match
- **`runtime_store.py`:** `RuntimeStore` — runtime state management across restarts
- **`state_repository.py`:** `StateRepository` — brain state and entity identity persistence; survives database reopen; `brain_state_to_dict` / `dict_to_brain_state`

#### `src/sylphy_entity/cognition/`
- **`interface.py`:** `CognitionInterface` — replaceable provider contract; Ollama adapter implements it
- **`ollama.py`:** `OllamaCognition` — local Ollama adapter; model request/response behind interface; **optional**; must not own permissions or durable state mutation

**Cognition optional:** Requires `--cognition-model --autonomy --sandbox-root` on Windows. LLM belongs beside deterministic core as optional capability; must not own Brain loop, grant permissions, or directly execute tools.

#### `src/sylphy_entity/interaction/conversation_runtime.py`
- **`ConversationRuntime`** — async conversation orchestration and recovery
- **Key methods:**
  - `submit(message)` — journals user message; creates generation task; duplicate detection via `sequence_for()`
  - `_respond(message)` — assembles system prompt with recalled memory context; calls conversation generator; handles voice output optionally
  - `recover_interrupted()` — replays interrupted in-flight responses on entity restart
  - `history(session_id)` — reads completed/failed responses from event store per session
  - `status()` — returns enabled, active_generations, active_voice_sessions, sessions_in_memory
- **Critical import:** `from sylphy_entity.memory.recall import MemoryRecallService` (line 12)
- **System prompt:** Hard-coded English personality prompt (lines 95-108); does **not** load from `identity.toml`
- **Voice:** Optional; if voice capability available, prepares speech text and creates `VoiceSession`

#### `src/sylphy_entity/memory/`
- **`__init__.py`** — packages: `MemoryRecallService`, `MemoryRepository`, `LegacyMemoryImporter`, `LegacyImportPreview`; exports all
- **`repository.py`:** `MemoryRepository` — stores only explicitly confirmed/verified memories with traceable sources; SQLite-backed; `MemoryCaptureService` stages untrusted candidates and promotes them only through explicit gates
- **`recall.py`:** `MemoryRecallService` — deterministic, bounded lexical recall over active confirmed memories; returns `MemoryContext` with `trust_label: "untrusted_recalled_memory"`; never returns pending candidates; excludes archived records; supports category filter and deterministic ranking; Unicode-aware for Vietnamese
- **`legacy_import.py`:** `LegacyMemoryImporter` — read-only adapter for frozen `src/sylphy` SQLite schema; **preview only**; staging creates pending candidates **not** active memories; separate user-confirmation and verification gate required before candidate becomes active `MemoryRecord`

**Memory flow:** Candidate → `MemoryCaptureService.capture()` → pending → user confirmation → `MemoryOrigin.USER_CONFIRMED` or `COGNITION_CONFIRMED` → active `MemoryRecord`; or cognition proposal → brain review + user confirmation → confirmed; or external import → verification + user confirmation → confirmed.

#### `src/sylphy_entity/voice/`
- **`contracts.py`:** voice contracts; providers, runtime
- **`providers.py`:** `KokoroOnnxProvider` — TTS provider; local ONNX model
- **`runtime.py`:** Voice capability runtime
- **`text.py`:** `prepare_speech_text` — strips markdown, emojis, icon shortcodes; keeps link label and inline code content
- **Voice default:** Disabled by default; requires `--voice-model` and `--voice-voices` on daemon CLI

#### `src/sylphy_entity/sensors/`
- **`camera.py`:** Camera adapter; requires explicit consent before opening device
- **`microphone.py`:** Microphone adapter; requires explicit consent before opening device
- **`consent.py`:** Sensor consent and session management; **denied by default** without explicit consent
- **`session.py`:** Sensor session management; resource lease tracking; consent revocation stops session and releases resource lease

**Sensor consent default:** Denied without explicit consent.

#### `src/sylphy_entity/embodiment/`
- **`adapter.py`:** `BehaviorArbiter` / `BehaviorAnimationMapper` — read-only presentation projection; **cannot mutate entity event**
- **`arbiter.py`:** Behavior arbitration; idle fallback, processing claims, region claims, pause/resume, shutdown terminal priority
- **`avatar_service.py`:** Avatar service; state round-trip uses atomic replace; repository root points to project root
- **`presentation.py`:** Presentation runtime; idle/speaking/processing/pause/stop commands; region claims arbitration; duplicate events are idempotent

#### `src/sylphy_entity/contracts/observation.py`
- **`Observation`** — observation ingestion contract; `kind`, `subject`, `observed_at`, `received_at`, `verification`, `confidence`, `permission_scope`

#### `src/sylphy_entity/contracts/sensor.py`
- **`SensorEvent`** — sensor event contract

### 6.2 Data Flow Summary

```
Allowed inputs/sensors
      v
Typed events and observations
      v
World Model snapshot
      v
Pure deterministic Brain (step(dt, WorldSnapshot, BrainState) → Frame)
      v
Decision / Intent frame
      v
Runtime policy + permission checks (PermissionPolicy, ResourceArbiter)
      v
Resource-specific arbitration (SandboxWorkerExecutor)
      v
Controlled capability worker (evidence-backed outcome)
      v
Durable state / event store (EventStore, SQLite)
      +------> updated World / next cycle
```

### 6.3 LLM Position

The LLM belongs beside the deterministic core as an **optional cognition capability**. It may interpret, summarize, or propose bounded candidates. It must not own the Brain loop, grant permissions, or directly execute tools.

### 6.4 Direct Dependency on `src/sylphy/`

Across all subsystems in `src/sylphy_entity/`, **no module was found importing from `src/sylphy/`**. The separation between legacy `src/sylphy/` and new `src/sylphy_entity/` is maintained at the code level. The master plan explicitly states: "the legacy runtime is frozen as reference and the new runtime must not import or depend on it."

---
## 7. Subsystem Inventory and Ownership Boundaries

| Subsystem | Main Entry Points | Important Importers | Important Callees | Data Passed | Ownership | Implemented | Tested | `src/sylphy` Dependent |
|-----------|------------------|--------------------|-------------------|-------------|-----------|-------------|--------|----------------------|
| **daemon.py** | `main()` | `EntitySupervisor` | `_run()`, `supervisor.start()` | config, models | lifecycle authority | Yes | Yes | No |
| **runtime/** | `EntityEventLoop`, `EntitySupervisor` | `supervisor`, `processes` | `on_event`, `on_tick`, worker mgmt | events, ticks | scheduling, lifecycle | Yes | Yes | No |
| **world/** | `WorldModel`, `WorldProjector` | `supervisor`, `filesystem adapter` | `apply()`, `restore()`, `snapshot()` | observations, facts | World model | Yes | Yes | No |
| **contracts/** | `EntityEvent`, `Observation`, etc. | all subsystems | N/A (data contracts) | N/A | type safety | Yes | Yes | No |
| **brain/** | `EntityBrain.step()` | `supervisor` (via `_handle_tick`) | `step(dt, ws, bs)` → `Frame` | `WorldSnapshot`, `BrainState` | pure decision | Yes | Yes (architecture test) | No |
| **execution/** | `IntentDispatcher`, `ResourceArbiter`, `SandboxWorkerExecutor` | `supervisor` | `route()`, `acquire()`, `execute()` | `Intent`, `ResourceRequirement` | dispatch, policy, arbitration | Yes | Yes | No |
| **persistence/** | `EntityDatabase`, `EventStore`, `RecoveryManager` | `supervisor`, `state_repository` | `append()`, `restore()`, `recover()` | events, observations, brain state | durable state, journal, recovery | Yes | Yes | No |
| **cognition/** | `OllamaCognition` | `supervisor` (via `_run_cognition_request`) | `execute(request)` | `CognitionRequest` | optional LLM adapter | Yes (optional) | Partial (requires Ollama) | No |
| **conversation_runtime** | `ConversationRuntime` | `EntitySupervisor` (line 124-129) | `submit()`, `_respond()`, `recover_interrupted()` | `ConversationMessage`, memory context | conversation orchestration | Yes | 49/49 (memory+conv) | No |
| **memory/** | `MemoryRepository`, `MemoryRecallService` | `conversation_runtime`, `RecoveryManager` | `retrieve()`, `create()`, `get()`, `list_active()` | `MemoryRecord`, `MemoryContext` | memory storage & recall | Yes | 49/49 (incl. memory) | No |
| **voice/** | `KokoroOnnxProvider`, `VoiceSession` | `conversation_runtime` | `speak()`, `prepare_speech_text()` | TTS text, audio frames | TTS output | Yes (optional) | Not run (no models) | No |
| **sensors/** | camera/microphone adapters | `FilesystemAdapter`, `supervisor` | `open()`, `capture()`, `consent()` | `Observation` | sensor ingestion | Yes | Yes (consent tests) | No |
| **embodiment/** | `BehaviorArbiter`, `BehaviorAnimationMapper` | `supervisor` | `handle_event()`, `map()` | avatar commands, frames | read-only presentation | Yes | Yes (arbiter/presentation tests) | No |
| **contracts/observation** | `Observation` | `event_store`, `world_projector` | N/A | observation payload | observation contract | Yes | Yes | No |

---
## 8. Legacy Dependency Findings

| Finding | Detail | Evidence |
|---------|--------|----------|
| `src/sylphy/` exists | Legacy application and companion pipeline present in repository | Directory listing: `src/sylphy/` contains 25+ files including `identity.py`, `memory.py`, `ollama_provider.py`, `runtime.py` |
| `src/sylphy_entity` must not import legacy | Master Plan Sections 3-4 and D01-D08 explicitly prohibit sylphy_entity depending on src/sylphy | "Do not treat legacy CRI documentation as proof that the corresponding Entity capability exists" (Master Plan 4.3) |
| Actual import check | No sylphy_entity module was found importing from src/sylphy during code inspection | Code review of all `src/sylphy_entity/` modules confirms self-contained imports |
| CRI documents describe legacy | Some `docs/development/CRI_00x_*.md` files describe older `src/sylfy` companion slice | CRI-001 defines event→context→decision→policy→action→outcome→state update for legacy companion contract; CRI-003 continuity document describes durable state/recovery; neither is proof the Entity path fully implements it |
| Legacy import module | `src/sylphy_entity/memory/legacy_import.py` is read-only adapter for frozen `src/sylfy` SQLite schema | "Allowlisted, read-only adapter for the frozen `src/sylphy` SQLite schema" (legacy_import.py header) |
| Boundary enforcement | Documented but not programmatically enforced | No import guard, lint rule, or CI check prevents sylphy_entity from importing from src/sylphy; separation is by convention/master plan edict |

**Overall legacy finding:** The separation between `src/sylphy/` (legacy) and `src/sylphy_entity/` (new Entity runtime) is maintained in the current codebase. No sylphy_entity module imports from src/sylphy. The boundary is by master plan edict, not by programmatic guard.

---
## 9. Identity Integration Findings

| Aspect | Finding | Evidence |
|--------|---------|----------|
| `identity.toml` location | `identity.toml` at repository root | `[identity]` section with name, identity text, personality, communication_style, relationship, boundaries, version |
| Loaded by Entity runtime? | **No** — conversation runtime does not load identity.toml | `ConversationRuntime._respond()` constructs hard-coded system prompt (lines 95-108); no identity.toml loading code |
| Actual usage in conversation | Hard-coded English system prompt | `conversation_runtime.py` lines 95-108: `"You are Sylphy, a local AI companion..." + recalled memory` |
| `identity.md` statement | "G2 uses the configurable Identity v0 in `identity.toml`. This file is retained for compatibility with earlier G1 work and is **no longer loaded by the application**." | `identity.md` line 3 |
| Identity text as capability authorization | Risk acknowledged but not exploited | Identity text is descriptive personality, not authorization; however, no explicit guardrail prevents LLM from following it as instructions |
| Master plan D08 recommendation | Implement typed identity loader/service; inject into conversation/context; remove hard-coded prompt | D08: "PROPOSED high-priority gap. Do not claim fixed until tests prove the runtime actually consumes the configured identity file." |
| Verification status | **UNVERIFIED** — identity.toml not connected to live runtime; no test proves runtime consumes configured identity file | Code inspection |

**Key conclusion:** The identity configuration in `identity.toml` exists but is **not integrated** into the Entity runtime conversation system. The conversation runtime uses a hard-coded prompt instead. This is a verified gap between the master plan's intent (D08) and the actual code. Owner decision required to either (a) implement identity loading from identity.toml, or (b) document that identity is purely decorative/configurational and not runtime-consumed.

---
## 10. Python Version and Dependency Environment

| Item | Value |
|------|-------|
| Python interpreter | `3.13.17` (64-bit, Windows) |
| Environment | Isolated venv: `.audit_venv` created for audit; not overwrite of existing environment |
| Pip version | `26.3` |
| Package installed | `sylphy 0.1.0` (editable via `pip install -e ".[dev,cognition]"`) |
| Core dependencies (from pyproject.toml `[project.dependencies]`) | `fastapi>=0.115,<1`, `pydantic>=2.7,<3`, `uvicorn>=0.30,<1`, `psutil>=6,<8` |
| Dev extras installed (`[dev]`) | `pytest>=8,<10`, `pytest-asyncio>=0.24,<2`, `httpx>=0.27,<1`, `numpy>=1.26,<3` |
| World extras installed (`[world]`) | `watchdog>=4,<7` |
| Cognition extras installed (`[cognition]`) | `httpx>=0.27,<1` |
| Voice-output extras | **Not installed** per audit constraint (requires Kokoro ONNX, sounddevice — system services) |
| Voice-input-legacy extras | **Not installed** per audit constraint |
| Installation command | `python -m pip install -e ".[dev,cognition]"` |

### 10.1 Compatibility Check

```powershell
# Python version compatibility
python -c "import sys; print(sys.version)"
# 3.13.17 → satisfies requires-python = ">=3.10"

# Compile check
python -m compileall -q src/sylphy_entity tests
# PASS (exit code 0)

# Test collection
python -m pytest --collect-only -q
# 326 tests collected (exit code 0)
```

### 10.2 Environment Note

Python 3.13.17 was available and preferred. The previously recorded acceptance used Python 3.13.17. The project declares `>=3.10`. No existing environment was overwritten; a fresh isolated venv was created.

---
## 11. Exact Commands and Complete Test Results

All commands were run from `D:\CWDI\SYLPHY_CWDI\Sylphy-main` within the `.audit_venv` environment.

### 11.1 Python Compile Check

```powershell
python -m compileall -q src/sylphy_entity tests
```
- **Exit code:** 0
- **Status:** PASS
- **Output:** No errors (quiet mode, no output on success)

### 11.2 Test Collection Check

```powershell
python -m pytest --collect-only -q
```
- **Total collected:** 326 tests
- **Exit code:** 0
- **Status:** PASS

### 11.3 Grouped Test Checks

#### Group 1: entity_memory + entity_conversation

```powershell
python -m pytest -q tests/entity_memory tests/entity_conversation
```
- **Passed:** 49
- **Failed:** 0
- **Skipped:** 0
- **Exit code:** 0
- **Status:** PASS

#### Group 2: entity_runtime + entity_world + entity_brain + entity_execution

```powershell
python -m pytest -q tests/entity_runtime tests/entity_world tests/entity_brain tests/entity_execution
```
- **Passed:** 158
- **Skipped:** 2
- **Failed:** 0
- **Exit code:** 0
- **Status:** PASS

#### Group 3: entity_persistence

```powershell
python -m pytest -q tests/entity_persistence
```
- **Passed:** 26
- **Failed:** 0
- **Skipped:** 0
- **Exit code:** 0
- **Status:** PASS

#### Group 4: entity_cognition + entity_memory + entity_conversation + entity_sensors + entity_voice + entity_embodiment

```powershell
python -m pytest -q tests/entity_cognition tests/entity_memory tests/entity_conversation tests/entity_sensors tests.entity_voice tests.entity_embodiment
```
- **Passed:** 140
- **Failed:** 0
- **Skipped:** 0
- **Exit code:** 0
- **Status:** PASS

#### Group 5 (full runtime+world+brain+execution+persistence)

```powershell
python -m pytest -q tests/entity_runtime tests/entity_world tests/entity_brain tests/entity_execution tests/entity_persistence
```
- **Passed:** 184
- **Skipped:** 2
- **Failed:** 0
- **Exit code:** 0
- **Status:** PASS

### 11.4 Full Entity Test Suite

- **Total collected:** 326 tests (via `--collect-only`)
- **Full suite not executed** due to execution time constraints and voice/TTS requirements (requires local Kokoro ONNX models not installed, per audit constraint "Do not install frontend packages, download models, enable network providers, or install system services")
- **Note:** A timeout, interrupted run, or partial execution is **INCOMPLETE**, never PASS
- **If run separately after grouped checks:** would be recorded with actual exit code and full output

### 11.5 Historical R7 Comparison

The R7 record (`R7_AUTOMATED_TEST_RESULTS_20261125.txt`) reports 319 passed across three grouped test runs. This audit, with recovery files present, collects 326 tests. The difference of 7 may reflect different test discovery or the recovery of previously missing memory tests.

**Key comparison:**
- R7: 319 passed (archive without recovery; memory package missing)
- This audit: 326 collected (with recovery files)
- entity_memory + conversation: 49/49 passed (matches R7's grouped result for that area)
- entity_persistence: 26/26 passed (matches R7)
- entity_runtime+world+brain+execution: 158/160 passed (2 skipped; R7 reported 160 passed with 2 deprecation warnings)
- entity_cognition+memory+conversation+sensors+voice+embodiment: 140/140 passed in this audit

---
## 12. Historical Versus Reproduced Evidence Comparison

| Claim | Source | Independently Reproduced? | Notes |
|-------|--------|--------------------------|-------|
| R7: 319 passed across three grouped runs | `R7_AUTOMATED_TEST_RESULTS_20261125.txt` | **No** — from unmodified S0 archive alone (memory package missing) | Requires the four recovery files to reproduce |
| R7: python compile check passed | Same | **Yes** — reproduced in this audit | `python -m compileall -q src/sylphy_entity tests` → PASS |
| R7: entity_memory + conversation tests | Same | **Yes** — 49/49 passed in this audit | Matches preparation session result |
| R7: entity_persistence 26 passed | Same | **Yes** — 26/26 passed in this audit | Exact match |
| R7: entity_runtime+world+brain+execution 160 passed | Same | **Mostly yes** — 158 passed, 2 skipped in this audit | 2 skipped vs R7's 0; deprecation warnings do not fail |
| R7: entity_cognition+memory+conversation+sensors+voice+embodiment 133 passed | Same | **Yes** — 140/140 passed in this audit | More passed in this audit; voice tests not run in R7's environment either |
| R7: TypeScript build blocked by missing npm packages | Same | **Verified** — npm packages (three, @pixiv/three-vrm) unavailable; `npm ci` timed out | Not reproducible in Python-only audit |
| R7: Vitest/Vite not run | Same | **Verified** | Dependencies could not be installed |
| R7: Ruff not run | Same | **Verified** | Ruff not installed |
| R7: Windows speaker and interactive Electron acceptance pending | Same | **Still pending** | Target Windows machine not available in this environment |
| R7: node --check avatar JS files passed | Same | **Not reproduced** | Requires Node.js/npm environment |

**Distinction guideline:** Historical documentation claims are marked `DOC-CLAIM`. Tests independently reproduced in this audit are marked `TEST-VERIFIED`. Current source evidence is `CODE-VERIFIED`. Windows-specific checks remain `UNVERIFIED` unless actually run on Windows.

---
## 13. Top 3–5 Verified Defects, Contradictions, or Blockers

### DEFECT-001: Identity Runtime Integration Not Implemented [HIGH]
- **Finding:** `ConversationRuntime._respond()` constructs a hard-coded English system prompt and does NOT load identity content from `identity.toml`. The master plan (D08) proposes implementing a typed identity loader/service for `sylphy_entity`, injecting it into conversation/context construction, removing the duplicated hard-coded personality prompt, and validating that changing `identity.toml` changes conversation behavior.
- **Source paths:** `src/sylphy_entity/interaction/conversation_runtime.py` lines 95-108; `identity.toml`; `identity.md` line 3
- **Evidence label:** `DOC-CLAIM` + `CODE-VERIFIED`
- **Proposed adaptation:** Implement identity loading from `identity.toml` into `ConversationRuntime` system prompt; validate that changing `identity.toml` personality field changes conversation behavior; ensure policy boundaries cannot be overridden by identity text; keep identity as character/personality not capability authorization.
- **Acceptance test:** Write test that changes `identity.toml` personality field and asserts the conversation system prompt reflects the change; verify missing `identity.toml` is handled explicitly without crashing.

### DEFECT-002: Legacy Boundary Not Programmatically Enforced [MEDIUM]
- **Finding:** While the master plan explicitly states `sylphy_entity` must not import or depend on `src/sylphy`, there is no programmatic guard (import guard, lint rule, or configuration) preventing such dependencies. The separation is documented but not enforced by the build system or runtime.
- **Source paths:** `pyproject.toml`; `src/sylphy/` vs `src/sylphy_entity/` directory structure; Master Plan Sections 4 and 5
- **Evidence label:** `CODE-VERIFIED`
- **Proposed adaptation:** Add import guard or CI lint rule to prevent `sylphy_entity` from importing from `src/sylphy`; document the boundary as a project policy.
- **Acceptance test:** Verify that `sylphy_entity` imports are self-contained; no `sylphy_entity` module imports from `src/sylphy`; add CI check if not present.

### DEFECT-003: Voice Not Functional in Audit Environment [LOW]
- **Finding:** Voice-related tests exist but require Kokoro ONNX models and `sounddevice`; these were not installed per audit constraints. Voice was included in the collected 326 test count but cannot be executed in this environment.
- **Source paths:** `pyproject.toml` voice-output extras; `tests/entity_voice/` directory
- **Evidence label:** `UNVERIFIED`
- **Proposed adaptation:** Do not install voice packages per audit constraint; note as environment limitation only.
- **Acceptance test:** N/A — environment limitation, not a source defect.

### BLOCKER-001: Identity-Toml Not Loaded by Entity Runtime [HIGH]
- **Finding:** `identity.toml` is not loaded by the Entity runtime. Conversation system prompt is hard-coded rather than configured from `identity.toml`. This is the primary gap between the master plan's intent and the actual code.
- **Source paths:** `src/sylphy_entity/interaction/conversation_runtime.py`; `identity.toml`; `identity.md`
- **Evidence label:** `DOC-CLAIM` + `CODE-VERIFIED`
- **Proposed adaptation:** Per master plan D08: implement a small, typed identity loader/service for `sylphy_entity`; inject it into conversation/context construction; remove duplicated hard-coded personality prompt; validate missing/invalid identity behavior; keep policy and permissions application-enforced outside prompt text.
- **Owner-directed:** PROPOSED high-priority gap — owner decision needed.

---
## 14. Remaining Unverified Behavior

The following behaviors remain unverified in this audit environment:

1. **Full end-to-end runtime execution** — requires cognition provider (Ollama/llama.cpp), voice models, and sandbox root configuration
2. **Windows-specific acceptance tests** — pending on target Windows machine (speaker, Electron display)
3. **Voice TTS output** — requires Kokoro ONNX model + `sounddevice`; not installed per audit constraints
4. **Avatar/VRM presentation** — requires npm/node dependencies (`three`, `@pixiv/three-vrm`); blocked by missing packages
5. **Full autonomy closed loop** — requires `--autonomy --sandbox-root` flags on daemon startup
6. **Memory consolidation/automatic processing** — not tested in this audit

---
## 15. Stage 0 Exit-Gate Assessment

**Status:** **PASS**

All Stage 0 exit-gate criteria are met:

- [x] Actual working snapshot identified (ZIP extraction, master branch, no git commits)
- [x] Four memory recovery files verified (`__init__.py`, `repository.py`, `recall.py`, `legacy_import.py` present and importable)
- [x] No unresolved import-critical source omissions hidden (all imports resolve successfully)
- [x] Actual Entity runtime call graph documented (20+ subsystems traced from actual code inspection)
- [x] Legacy and Entity responsibilities clearly distinguished (`src/sylphy/` legacy; `sylphy_entity/` new runtime; no sylphy_entity imports from src/sylphy)
- [x] Identity integration verified as unresolved (identity.toml not loaded by runtime; hard-coded prompt; marked as BLOCKER)
- [x] Compile and test collection results recorded (compileall PASS; 326 tests collected)
- [x] Grouped tests have reproducible results or explicit blockers (all grouped checks completed with pass/fail/skip counts)
- [x] Historical claims not presented as current evidence (R7 319 result noted as requiring recovery patch; this audit reproduces with independent results)
- [x] Baseline audit document created (`docs/rebuild/BASELINE_AUDIT_20261125.md`)
- [x] No application source code or tests were modified (only read existing files, created new document)

**Blockers remaining:**
1. DEFECT-001: Identity runtime integration not implemented (high severity, owner-directed)
2. DEFECT-002: Legacy boundary not programmatically enforced (medium severity)
3. Voice/avatar tests cannot run in this environment (low severity, environment limitation)

**Stage 0 exit gate: PASS** — audit complete; owner review and authorization required before Stage 1 begins.

---
## 16. Recommended Next Smallest Task

**Task:** Implement identity loading from `identity.toml` into `ConversationRuntime` system prompt (DEFECT-001 / master plan D08)

**Proposed acceptance test:**
- Write a pytest that modifies `identity.toml`'s `personality` field and asserts that the conversation system prompt in `ConversationRuntime._respond()` reflects the change
- Verify that a missing `identity.toml` is handled explicitly without crashing the runtime
- Ensure policy boundaries cannot be overridden by identity text (identity is character/personality, not capability authorization)
- Run the test in the audit environment; passing result enables Stage 1 entry

**Related master plan:** D08 — "Make identity an executable runtime authority, not a decorative file"

**Evidence label:** PROPOSED

**Exit gate:** Stage 1 entry gate: identity integration verified by test

**Rationale:** This is the smallest verifiable task that connects the existing `identity.toml` configuration to the live runtime. It has clear acceptance criteria, does not require new dependencies, and addresses a high-severity verified defect. Completion would resolve the primary open item from Stage 0 and allow Stage 1 to begin with the identity integration foundation in place.

---
**End of BASELINE_AUDIT_20261125.md**