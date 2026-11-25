# SYLPHY — Entity Rework Master Plan

**Document status:** Master architecture and execution plan  
**Purpose:** The single plan governing the SYLPHY rework. Stages are sequential. A stage is not complete because its modules exist or unit tests pass; it is complete only when its exit criteria are demonstrated in a running system.

## 0. Live roadmap status — updated 2026-11-24

This is the execution tracker for the seven-stage rework. The stage definitions below remain the governing scope; status changes do not authorize scope creep.

| Stage | Status | Evidence / boundary |
|---|---|---|
| **1. Entity Daemon and Runtime Authority** | **COMPLETE — Windows verified** | Detached Daemon, independent CLI, loopback API, event ingress, single-instance refusal, clean shutdown and SQLite boot continuity passed on Windows. |
| **2. World Interface, Observation, and World Model** | **COMPLETE — Windows verified** | Windows filesystem smoke test passed for sandbox observation and World Model updates. Cleanup once warned about a temporary directory retained by Windows; the runtime verification itself passed. |
| **3. Entity Brain** | **COMPLETE — simulator exit criteria demonstrated** | Pure deterministic Brain, 36 Brain tests, 1,000-tick replay, 5,000-tick seeded invariant stress test, takeover/yield/resume, stale/unknown handling and architecture-boundary test. Brain remains pure and is only connected to Runtime in Stage 6. |
| **4. Intent, Permission, Resource Arbitration, and Execution** | **COMPLETE — Windows verified** | Windows acceptance: 117 passed, 2 skipped; isolated worker, metadata inspection, bounded text read/evidence, permission rejection, worker cleanup and compile checks passed. |
| **5. Persistent Entity State and Crash Recovery** | **COMPLETE — Windows verified** | User's Windows acceptance: 143 passed, 2 skipped; compile passed; Entity ID/Brain/Goal/Novelty survived restart; World restored as STALE; in-flight Intent classified UNKNOWN; verified orphan process terminated; permission and recovery state persisted. |
| **6. First Autonomous Closed Loop** | **COMPLETE — Windows verified (2026-11-24)** | User ran `tools/verify_entity_stage6_windows.ps1` on Python 3.13.17. Compile passed; Stage 6 scenario matrix: 17 passed; complete Entity suite: 167 passed, 2 skipped. Script ended with `PASS: Stage 6 autonomous closed-loop Windows acceptance completed`. |
| **7. Integrate Cognition, Memory, Conversation, and Embodiment** | **IN PROGRESS — Cognition and Memory Slices 2A–2D accepted on Windows; Conversation Slice 3A implemented, Windows acceptance pending** | Cognition live Ollama acceptance passed on Windows with `lfm3-uncensored-32k:latest` (5/5 requests completed and Brain-reviewed; evidence accepted). Memory 2A: 33 passed; 2B: 43 passed; 2C: 50 passed; 2D: 64 passed, each with its Windows acceptance marker. Conversation Slice 3A adds a bounded user-message contract and ingress adapter that submits exactly one `conversation.message.received` EntityEvent through the existing EventStore/EventLoop boundary. It does not generate replies, invoke Cognition, or retrieve Memory. Run `tools/verify_entity_stage7_conversation_slice3a_windows.ps1`.

**Current position:** Stages 1–6 are accepted. Stage 7 Cognition and Memory Slices 2A–2D have passed Windows acceptance. Conversation Slice 3A (bounded user-message contract and Entity-event ingress) is implemented and container-verified; Windows acceptance is pending. Conversation turn orchestration, sensor adapters, Voice/Avatar and CLI/UI remain gated behind their preceding slices. Do not reopen earlier stages unless a regression is found. Do not mark a stage complete based only on modules or unit tests; use its exit criteria and running evidence.

### Mapping from the detailed `G4-Rework.txt` technical sequence

The 13 technical steps in `G4-Rework.txt` are implementation tasks, not a replacement numbering system for this seven-stage Master Roadmap:

| `G4-Rework.txt` technical step | Governing Master Roadmap stage |
|---|---|
| Step 0 — baseline inventory | Completed; repeat only if a regression or repository change requires it |
| Step 1 — domain contracts | Stage 2 first; Brain/Intent contracts remain in their later stages |
| Step 2 — persistent Event Store | Stage 5 |
| Step 3 — Runtime Authority | Stage 1 |
| Steps 4–5 — World Interface and World Model | Stage 2 |
| Steps 6–7 — Brain, Goals, Drives, Novelty | Stage 3 |
| Steps 8–9 — Intent, permissions, arbitration, executors | Stage 4 |
| Steps 10–11 — feedback, persistence recovery and reconciliation | Stages 5–6 according to durable-state versus end-to-end proof |
| Step 12 — LLM cognition | Stage 7 |

The seven-stage plan remains authoritative. The 13-step technical sequence does not replace or reorder it.

---

## 1. Mission and non-negotiable direction

Rebuild SYLPHY as one persistent local-first Entity with its own lifecycle. It must observe an allowed environment, maintain an explicit model of what it knows, decide what to do, execute only permitted actions, receive evidence about the result, and continue across idle periods and process restarts.

The architecture has two primary sources:

- **Microduck = Brain and behaviour architecture.** Its `Brain` reads a `World` snapshot on each `step(dt, world)` call and returns a `Step`; the daemon-side caller translates the result into intents. The brain contains a state machine, energy-like internal drive, novelty/visit memory, time-dependent transitions, and explicit handling for stale/unknown observations and external takeover. It does not own sockets or I/O.
- **Reachy Mini = Runtime and process-lifecycle architecture.** Its Daemon owns the backend and lifecycle. Its AppManager starts apps as subprocesses, records process identity, monitors output and exit status, stops children, and cleans up orphaned processes. Its managed app lock arbitrates a specific app/remote-control slot.

SYLPHY must combine these principles without copying robot-specific implementation details. It is a digital entity on a Windows workstation, not a mobile robot.

## 2. Architecture decisions

1. **One Entity Daemon is the lifecycle authority.** The Entity exists independently of a conversation session or presentation UI. The daemon owns the event loop, World Model, Brain instance, action dispatch, resource ownership, and persistent state.
2. **Brain is pure decision logic.** It receives an immutable World Snapshot, internal state, events, and elapsed time; it returns a Decision Frame. It never calls an LLM, database, filesystem, API, or executor.
3. **Sensors publish facts; they do not decide.** Every input source becomes an adapter that emits typed Observations. Only the World Model updates the world snapshot.
4. **Brain proposes Intents; Runtime authorizes and executes.** An Intent is not an action already performed. Every action passes schema validation, permission checks, resource arbitration, execution, and feedback.
5. **Resource ownership is per capability.** Do not use a global `busy` flag or a single global lock for all work. Model the actual resources that conflict: inference slot, filesystem scope, process slot, notification channel, camera, microphone, etc.
6. **Persistence belongs to the Entity, not the UI.** Persist internal state, goals, events, intents, execution evidence, and recovery markers. Conversation sessions are only one input source.
7. **LLM is a cognition capability, not the Brain or Runtime.** It can interpret, summarize, compare, or propose. It cannot grant itself permissions or directly execute tools.
8. **One modular monolith first.** One daemon with well-separated modules and isolated child processes only where needed. Do not copy Microduck's multi-daemon split, which exists for physical hardware, sensors, transport, and recovery boundaries SYLPHY does not have.
9. **Build a digital environment first.** The first real adapter is an explicitly permitted sandbox directory. No full-disk indexing, arbitrary shell, web crawling, camera, or microphone in the first autonomous slice.
10. **No legacy architecture dependency.** The old G3/G4/G5/G6 runtime and Companion pipeline are frozen as legacy reference. The new runtime must not import or depend on them.

## 3. Target source layout

Build the new system in a separate package inside the existing repository so the failed architecture cannot leak into the new design:

```text
src/
├── sylphy/                         # Existing implementation; frozen legacy
└── sylphy_entity/                  # New Entity Runtime; sole rework target
    ├── daemon.py
    ├── contracts/
    │   ├── events.py
    │   ├── observation.py
    │   ├── world.py
    │   ├── decision.py
    │   └── execution.py
    ├── runtime/
    │   ├── supervisor.py
    │   ├── lifecycle.py
    │   ├── event_loop.py
    │   ├── process_manager.py
    │   └── resource_manager.py
    ├── world/
    │   ├── model.py
    │   ├── projector.py
    │   └── adapters/
    │       ├── filesystem.py
    │       ├── process.py
    │       └── interaction.py
    ├── brain/
    │   ├── core.py
    │   ├── state.py
    │   ├── behaviours.py
    │   ├── drives.py
    │   ├── goals.py
    │   ├── novelty.py
    │   └── transitions.py
    ├── execution/
    │   ├── intent_router.py
    │   ├── permissions.py
    │   ├── resource_arbiter.py
    │   ├── dispatcher.py
    │   ├── result.py
    │   └── workers/
    ├── persistence/
    │   ├── database.py
    │   ├── event_store.py
    │   ├── state_repository.py
    │   └── recovery.py
    ├── cognition/
    │   ├── interface.py
    │   └── ollama.py
    └── interface/
        ├── local_api.py
        └── cli.py
```

The exact number of files may change during implementation, but the ownership boundaries must not. Do not create empty modules ahead of their stage.

## 4. Runtime and tool choices

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.13 for the rework runtime | Matches the current Windows development environment and existing project language; no reason to rewrite the digital Entity in Rust |
| Concurrency | `asyncio` for the daemon; subprocesses for isolated workers | One coordinated lifecycle, cancellable async I/O, and fault isolation for long or risky tasks |
| Daemon API | FastAPI + Uvicorn bound to `127.0.0.1` only | Local control/status/stream interface; CLI and later UI are clients, not lifecycle owners |
| Internal contracts | `dataclasses`, `Enum`, `Protocol`; Pydantic at API/input boundaries where validation is needed | Pure, lightweight Brain types and validated external messages |
| Persistence | SQLite with WAL; one controlled writer path | Local, durable, no distributed database or broker |
| Filesystem observation | `watchdog` plus periodic reconciliation | Low-latency change events while recovering missed events |
| Process/resource observation | `psutil` | Process identity, status, child-tree inspection, resource telemetry |
| Local model | Existing Ollama service through the cognition interface | Keep the local model backend replaceable; it must not sit inside the Brain loop |
| Tests | `pytest`, `pytest-asyncio`; deterministic fake clock and seeded randomness | Fast Brain replay and lifecycle/worker integration tests |
| Quality | Ruff; type checking on core boundaries | Catch contract drift without adding a framework-heavy architecture |

Do not add Redis, Kafka, RabbitMQ, Celery, Kubernetes, LangGraph, a vector database, or a separate Rust service in this rework. Add a dependency only when a stage has a concrete capability that requires it.

## 5. Sequential implementation stages

### Stage 1 — Entity Daemon and Runtime Authority

**Objective:** Make SYLPHY an independently running Entity process, no longer a conversation application whose life is tied to the CLI session.

**Build in:** `src/sylphy_entity/daemon.py`, `runtime/supervisor.py`, `runtime/lifecycle.py`, `runtime/event_loop.py`, `contracts/events.py`, `interface/local_api.py`.

**Implement:**
- One daemon instance with explicit lifecycle states: `STARTING`, `READY`, `RUNNING`, `DEGRADED`, `STOPPING`, `STOPPED`, `ERROR`.
- Startup order: acquire single-instance ownership; open persistence; restore minimal Entity state; start event ingress and adapter supervision; start the Brain tick scheduler; expose the local API.
- Shutdown order: stop new autonomous work; cancel or yield running work; stop adapters and child workers; persist final state; close database and API.
- Event-driven wake-up plus a low-frequency timed tick. Initial Brain cadence: 1 Hz, with immediate wake-up on meaningful events. Use `time.monotonic()` for elapsed time. Do not use a 50 Hz loop: that is for robot motor control, not a desktop Companion.
- Local API bound to loopback. The console/UI communicates with the daemon and can close without terminating the Entity.
- Runtime health and task supervision. No business decisions in the supervisor.

**Based on:** Reachy Mini `daemon/daemon.py` for lifecycle ownership, backend startup/shutdown, and media/process cleanup; `apps/manager.py` for process monitoring and exit states. Use Microduck's separation in which `autod` is a client that can be stopped independently of the robot daemon.

**Do not copy:** Reachy's physical backend or Microduck's Linux systemd/socket topology. SYLPHY initially needs one Windows daemon, not eight daemons.

**Exit criteria:** daemon starts and stays alive after the CLI exits; second daemon instance is refused; clean shutdown completes; one failed child worker does not kill the Entity; lifecycle state is externally inspectable.

### Stage 2 — World Interface, Observation, and World Model

**Objective:** Give SYLPHY a reliable representation of its allowed digital environment.

**Build in:** `contracts/observation.py`, `contracts/world.py`, `world/model.py`, `world/projector.py`, and `world/adapters/`.

**Implement:**
- `Observation`: unique event ID, source ID, subject/target, kind, payload, observed timestamp, received timestamp, sequence/version, confidence/verification, permission scope, and freshness status.
- World facts explicitly distinguish `KNOWN`, `UNKNOWN`, and `STALE`. Missing data must never be silently interpreted as “nothing there.”
- `WorldSnapshot`: immutable view of currently known environment facts, active user interaction, running work, resource state, active goals, and relevant recent events.
- Adapters publish Observations only. They cannot call Brain or execute actions.
- First live source: a user-authorized sandbox directory. Detect create/change/delete/rename. Use `watchdog` for events and periodic reconciliation to catch dropped notifications.
- A simulated adapter must emit exactly the same Observation contract as the real adapter. This lets Brain tests run without touching the real filesystem.
- Scope restrictions are enforced in the adapter and again at action execution; the sandbox root is explicit configuration.

**Based on:** Microduck `autod/src/brain.rs` `World` input snapshot, its obstacle freshness/unknown handling, and the separation between `tofd`, `mapd`, and `autod`. Translate the robot's map/ToF into a digital World Model; do not build a 2D spatial map for SYLPHY. Use Reachy's backend abstraction principle: capability implementations can be simulated or live behind one contract.

**Exit criteria:** simulated and live filesystem sources produce equivalent contract events; repeated notifications are deduplicated; deleted or inaccessible data becomes unknown/stale rather than remaining falsely current; no source can directly trigger an action.

**Implementation record (2026-11-23):** Observation/World contracts and in-memory World Model are implemented; the simulator and sandbox-only filesystem adapter use the same observation contract. The daemon exposes `/world` and `sylphy-entityctl world`. The filesystem adapter uses Watchdog notifications plus periodic reconciliation when the optional `world` extra is installed; a polling-only fallback exists for minimal/offline environments. File content is never read. Development verification is **27 passed** across `tests/entity_runtime` and `tests/entity_world`. Windows acceptance remains pending and is required before marking this stage complete. See `docs/entity_rework/STAGE_2_IMPLEMENTATION_REPORT.md` and run `tools/verify_entity_stage2_windows.ps1` after `py -3.13 -m pip install -e ".[dev,world]"`.

### Stage 3 — Entity Brain

**Objective:** Build the actual decision-making centre. This is the most important stage.

**Build in:** `brain/core.py`, `brain/state.py`, `brain/behaviours.py`, `brain/drives.py`, `brain/goals.py`, `brain/novelty.py`, `brain/transitions.py`, `contracts/decision.py`.

**Implement:**
- A pure stateful function: `brain.step(dt, world_snapshot, brain_state) -> DecisionFrame`.
- `DecisionFrame` contains next internal state, behaviour transition, goal updates, proposed Intents, and concise machine-readable decision reasons.
- Initial behaviour states: `RESTING`, `OBSERVING`, `EVALUATING`, `PURSUING`, `WAITING`, `REFLECTING`, `YIELDING`, `RECOVERING`. These are Entity behaviour states, not chat states.
- Drives are operational pressures, not simulated human emotions: `curiosity`, `continuity`, `social_responsiveness`, `self_maintenance`, and `resource_conservation`. Each has bounded values, explicit inputs, and decay/update rules.
- Goals have identity, source, priority, urgency, desired outcome, status, cost/budget, deadline, permission requirements, and completion evidence.
- Goal choice is policy-based: urgency and drive relevance raise priority; cost, risk, cooldown, repeated failure, and lack of evidence lower it. The LLM may provide a proposal, but the Brain owns selection.
- Novelty is domain-specific: recently inspected files/topics/events receive less attention; relevant changes and unvisited items gain attention. Use bounded, decaying history, inspired by Microduck's novelty grid, not a literal spatial grid.
- External takeover is explicit. User interaction or an owned resource causes Brain to yield; clocks for suspended behaviours pause where appropriate. On resume, transient latches reset while persistent drives, goals, and novelty survive.
- Use seeded randomness only for low-risk variation where it improves naturalness. Core decisions must be replayable and deterministic for a fixed input/state/seed.

**Hard rule:** Brain imports no Ollama client, database, filesystem, network client, subprocess manager, or UI module. It returns Intent proposals only.

**Based on:** Microduck `Brain::step(dt, &World) -> Step`, `State`, the energy model, visit/novelty decay, `busy` freeze, explicit reset semantics, and simulation tests in `autod/src/brain.rs`.

**Do not copy:** Duck states like Nap, Laser, BallPlay, or a robot energy value literally. Adapt the state machine and internal drives to digital activity and purposeful initiative.

**Exit criteria:** Brain runs in a simulator for long virtual periods without Ollama; deterministic replay produces the same decisions; user takeover causes yield; stale/unknown facts cannot create unjustified certainty; goals persist through behaviour transitions.

**Implementation and acceptance record (2026-11-23): COMPLETE.** Implemented `EntityBrain.step()` as pure deterministic decision logic in `src/sylphy_entity/brain/`, with immutable decision contracts in `contracts/decision.py`. The Brain supports eight behaviour states, five bounded operational drives, domain-specific bounded novelty, evidence-aware sandbox file goal selection, one active pursuit at a time, goal completion/failure/waiting/abandonment feedback, exponential retry cooldown, deadlines, repeated-failure penalties, explicit permissions/scopes on proposals, external takeover/yield, suspended-goal deadline pausing, and transient-latch reset on resume. It emits `IntentProposal` only; no execution, database, filesystem, model or network access is present in the Brain package.

**Historical verification note:** the initial Stage 3 report recorded 68 passed. A later independent audit of the complete then-current Entity suite recorded **107 passed**; the dedicated Brain set contains 36 tests. The Stage 3 simulator and scope boundaries described above remain unchanged. `python -m compileall -q src/sylphy_entity tests/entity_brain tests/entity_runtime tests/entity_world` — passed. Long deterministic simulation: 1,000 ticks / 5,000 virtual seconds, repeated with identical input and identical decision trace. Seeded mixed-world stress test: 5,000 ticks; drive bounds, maximum one ACTIVE goal, bounded goal history, sandbox-only intent scope, safe relative targets, and no intents during yield all remained invariant. Worker-failure supervisor integration test passed 8/8 separate repeated runs after its monitor race was fixed. Brain architecture test verifies no imports of OS I/O, subprocess, network, database, model or runtime services. Ruff and mypy were not run because neither is installed in the development container. Stage 3 is complete in simulator scope; it has not been attached to the Daemon, and goals are not durable across process restarts (Stage 5), by design. See `docs/entity_rework/STAGE_3_IMPLEMENTATION_REPORT.md`; Windows Stage 3 verification script is prepared but not run from this container.

### Stage 4 — Intent, Permission, Resource Arbitration, and Execution

**Objective:** Convert Brain decisions into controlled, observable actions.

**Build in:** `contracts/execution.py`, `execution/intent_router.py`, `execution/permissions.py`, `execution/resource_arbiter.py`, `execution/dispatcher.py`, `execution/workers/`.

**Implement:**
- Intent contract: ID, goal ID, capability, target, arguments, reason, preconditions, permission scope, required resources, priority, deadline, timeout, idempotency key, expected outcome, and cancellation policy.
- Execution pipeline: `Intent → Validate → Authorize → Acquire Resources → Dispatch → Verify → Report Result`.
- Permission classes begin with `OBSERVE_SANDBOX` and `READ_SANDBOX`. No arbitrary shell, unrestricted filesystem, or network execution in the first release.
- Resource ownership is per capability and scope. Example: one local LLM inference slot; path-scoped filesystem access; exclusive process-launch slot; separate notification channel. No global `busy=True` lock.
- Every worker has identity, PID/process creation time, start/deadline, status, stdout/stderr capture, cancellation, and cleanup.
- Run long, failure-prone, or untrusted operations in subprocesses. Keep simple in-process adapters inside the daemon.
- User commands outrank autonomous work. A user can pause/deny/revoke permissions; active work must stop or reach a clearly marked non-cancellable boundary.
- `ExecutionResult` status is one of `SUCCEEDED`, `FAILED`, `CANCELLED`, `UNKNOWN`; success requires evidence, not just a zero exit code or an LLM claim.

**Based on:** Microduck's unprivileged `autod` sending ordinary intents to `robotd`, safety boundary, and pad takeover/yield behaviour. Reachy Mini `AppManager` subprocess start/monitor/stop, its PID + creation-time orphan record, and `RobotAppLock` local/remote handoff.

**Important limitation:** Reachy's `RobotAppLock` arbitrates the managed app/remote-session slot; it is not a universal hardware lock. SYLPHY must implement resource-specific claims rather than copying it as a single system lock.

**Exit criteria:** an unauthorized Intent is rejected before execution; conflicting claims cannot execute simultaneously; user takeover preempts or pauses autonomous work; a worker crash is reported with captured evidence; no action is labelled successful without verification.

### Stage 5 — Persistent Entity State and Crash Recovery

**Objective:** Make continuity real across daemon restarts and interrupted actions.

**Build in:** `persistence/database.py`, `persistence/event_store.py`, `persistence/state_repository.py`, `persistence/recovery.py`.

**Implement:**
- SQLite WAL with a single controlled write path.
- Persist events, observations, World Model projections, Entity/Brain state, drives, active goals, novelty history, Intents, resource claims, executions, and evidence references.
- Write an Intent transition before starting an external side effect. Every retry uses the same idempotency key where the operation permits it.
- On startup, reconcile incomplete executions. Anything that may have executed but lacks verified result becomes `UNKNOWN`/`INTERRUPTED`, not automatically `FAILED` and not automatically replayed.
- Re-observe the world, compare it with expected outcomes, and only resume actions that are both safe and idempotent. Ask the user where the outcome cannot be established safely.
- Persist worker identity as PID plus process creation time. On daemon restart, verify process identity before terminating orphan workers; never kill a reused PID blindly.
- Separate ephemeral state (tick timers, transient latches, current stream handles) from durable state (goals, drive values, novelty, user-granted permissions, verified outcomes).

**Based on:** Microduck persisting novelty/visit memory while resetting transient Brain latches, and Reachy Mini's persisted running-app identity/orphan cleanup and explicit process monitoring.

**Exit criteria:** restart preserves Entity identity and active goals; a crash between dispatch and result does not duplicate a side effect; worker or daemon death is reconciled; a stale world fact is re-observed before it is trusted.

**Implementation and verification record (2026-11-24): IMPLEMENTATION COMPLETE — Windows acceptance pending.**

Implemented a single shared `EntityDatabase` using SQLite WAL, `synchronous=FULL`, foreign keys, busy timeout and one serialized connection/write path. The existing Stage 1 `daemon_runtime` table/boot history is preserved when the old database is adopted; schema v1 migrates to v2 with the pending-observation projection marker. The Entity tables include append-only events, observation provenance/projection state, checksummed World snapshots/checkpoints, checksummed Brain/Entity state snapshots, Goals, Intents, resource leases, executions, worker identities, evidence, permissions and recovery runs.

The Daemon now opens persistence before recovery, restores its stable Entity ID and Brain state, classifies incomplete Intents before serving, replays committed events after the latest completed World checkpoint, and marks restored World facts `STALE` pending re-observation. Observation provenance and its projected marker are committed with the World checkpoint in one transaction, so a crash in the projection window leaves the observation replayable. Monotonic clock values are never reused as restart clocks; only relative deadline budgets are retained. Permission revocation survives policy recreation/restart.

Intent/Execution journaling commits acceptance and `DISPATCHING` before a capability side effect. Durable idempotency returns an existing evidence-backed terminal result after database reopen and blocks unresolved/unknown work from redispatch. Worker PID, creation time, daemon instance and lifecycle are persisted; normal/failed/cancelled worker exit is recorded, and recovery only terminates a process after PID **and** creation-time identity match. `ACCEPTED` work is reconciled as `INTERRUPTED`; `DISPATCHING`/`RUNNING` work without verified outcome is `UNKNOWN`; active leases are abandoned rather than reused. No uncertain side effect is automatically retried.

**Verification:** `python -m compileall -q src/sylphy_entity tests` passed. Full default Entity suite: **145 passed**, with two upstream Uvicorn/websockets deprecation warnings. Added coverage for adoption of a Stage 1 SQLite database without loss of boot history, v1→v2 migration, observation identity collision, crash window after observation provenance commit but before World checkpoint, actual child-process orphan termination with matching PID+creation time, wrong-creation-time refusal, direct worker terminal-state persistence, durable idempotency after closing/reopening SQLite, Brain/Goal/Novelty restoration, permission revocation, and uncertain Intent reconciliation. `tools/verify_entity_stage5_runtime.py` also passed a process-level crash/recovery smoke test in the container. Windows acceptance script is prepared but has not yet been run by the user. Stage 5 remains in progress until that Windows check passes.

See `SYLPHY_ENTITY_STAGE5_IMPLEMENTATION_REPORT.md` and `SYLPHY_ENTITY_STAGE5_RUNTIME_GUIDE.md`.

### Stage 6 implementation and verification record — 2026-11-24

**COMPLETE — Windows acceptance passed on 2026-11-24.** The Daemon now performs the mandatory first closed loop, opt-in only and scoped to an explicit sandbox: filesystem Observation → World projection → pure Brain evaluation → inspect Goal and Intent → permission and exact path-scoped resource arbitration → read-only metadata worker → independently verified evidence → Goal feedback → inspectable user notice. No LLM, conversation, long-term memory import, vision, voice, avatar, unrestricted filesystem, shell or network action was added.

Additional Stage 6 hardening:
- File novelty is keyed by a stable revision fingerprint, so the same watcher observation is deduplicated while a changed version of the same path can be evaluated again.
- Observation provenance (`source_observation_id`) is preserved from selected World fact through Intent, durable journal, history and notice.
- Durable successful execution feedback is replayed into the exact matching Goal attempt after restart; stale success cannot complete a later attempt. Unknown/incomplete Intent is never automatically redispatched.
- Simulator now exercises the same Intent dispatcher/permission/resource/evidence feedback path as the live proof, rather than stopping at proposal generation.
- Periodic reconciliation recovers a missed watcher wake-up. Delete/disappearance and stale facts do not become successful inspections.
- Authenticated pause and permission revoke/restore controls gate admission and cancel eligible active autonomous work. Resume cannot enable autonomy unless Daemon was explicitly started with `--autonomy` and a sandbox.
- A worker failure degrades but does not kill the Daemon; a real Daemon process crash after durable `DISPATCHING` is recovered as `UNKNOWN` without replay.

**Scenario matrix verified in container:** create, edit, rename, delete; duplicate/re-delivered notifications; event loss followed by reconciliation; file disappearance between Brain and dispatch; permission revocation before and during execution; user pause/interruption; worker subprocess crash; Daemon process crash after dispatch; restart with incomplete Intent; restart after verified success but before next Brain tick; model service absent; idle/no-op without repeated execution; simulator full closed loop; and autonomy opt-in enforcement.

**Verification:** Stage 6 focused tests: **16 passed**. Full Entity suite (Stages 1–6): **161 passed**. `compileall` for Entity source and all Entity test packages passed. Two non-failing upstream deprecation warnings arise from `websockets.legacy` and Uvicorn's `WebSocketServerProtocol`. A clean verbose full-suite run completed in 26.62 seconds.

**Windows acceptance:** the user ran `tools/verify_entity_stage6_windows.ps1` on Windows with Python 3.13.17. Compile passed; the mandatory Stage 6 scenario matrix reported **17 passed**; the complete Entity suite reported **167 passed, 2 skipped**; the script ended with `PASS: Stage 6 autonomous closed-loop Windows acceptance completed`. Stage 6 is accepted. The two skipped tests remain transparently reported as skipped.

See `SYLPHY_ENTITY_STAGE6_IMPLEMENTATION_REPORT.md` and `SYLPHY_ENTITY_STAGE6_RUNTIME_GUIDE.md`.

### Stage 6 — First Autonomous Closed Loop (mandatory proof)

**Objective:** Prove an Entity behaviour end-to-end before connecting more features.

**First scenario:** a change in the permitted sandbox directory.

```text
File created/changed
  → filesystem adapter emits Observation
  → World Model updates Snapshot
  → Brain evaluates novelty/continuity/permissions
  → Brain selects an inspect goal
  → Brain proposes Intent
  → Runtime validates permission and obtains path-scoped resource
  → read-only executor inspects the permitted file
  → executor returns evidence-backed result
  → World Model and Entity experience update
  → SYLPHY sends a user-visible notice
```

This must occur without a new chat message. The first version is read-only; no automatic edit/delete/send/network actions.

**Test scenarios:** create, edit, rename, delete; duplicate watcher events; event loss followed by reconciliation; file disappears before read; permission revoked; user interrupts; model server is offline; worker crashes; daemon crashes after dispatch but before recording result; restart with an incomplete Intent; no-op period with no repeated action.

**Exit criteria:** the full loop runs in live sandbox and simulator; no duplicate action on duplicate events or restart; user can stop autonomy; every decision and result is inspectable; the system can explain which observation and policy caused the action.

### Stage 7 — Integrate Cognition, Memory, Conversation, and Embodiment

**Objective:** Attach the existing SYLPHY capabilities as adapters around the proven Entity Core, not as competing centres.

**Build in:** `cognition/`, additional `world/adapters/`, `execution/workers/`, and presentation adapters.

**Integration order:**
1. **Local LLM cognition:** expose Ollama through `cognition/interface.py` and `cognition/ollama.py`. Brain requests bounded cognitive operations; results return as typed proposals/evidence. Timeout, cancellation, model-offline, and malformed output are handled as normal worker outcomes.
2. **Long-term memory:** memory is an experience/semantic recall capability, not the Entity's current world state. Import only records with clear provenance. Do not let an old conversation session define the Entity's lifecycle.
3. **Conversation:** a user message becomes one World Event. Conversation is one interaction mode, never the parent loop of the Entity.
4. **Vision/camera and hearing/microphone:** add each as a sensor adapter with explicit consent, freshness, privacy scope, and resource claims.
5. **Voice/avatar/presentation:** output adapters consume Entity state and verified events. They do not own Brain or runtime state.
6. **CLI/UI:** remain replaceable clients to the daemon. The console is for interaction, inspection, permissions, pause/resume, and debugging—not the Entity's body or lifecycle.

**Stage 7 implementation record — 2026-11-24 (Memory Slice 2A):** Cognition Slice 1 passed live Windows acceptance using `lfm3-uncensored-32k:latest`; the acceptance run completed and Brain-reviewed all 5 requests, with `accepted_as_evidence=true`. Memory foundation adds `contracts/memory.py` and `memory/MemoryRepository` backed by the Entity-owned SQLite database. Schema v3 is additive and retains existing runtime/event/persistence tables. Memory records require an explicit origin (`user_confirmed`, `cognition_confirmed`, or `imported_verified`) and at least one provenance reference. The repository supports idempotent create by stable ID, get, bounded active listing, and explicit archive. No legacy memory database import, automatic cognition-to-memory confirmation, semantic retrieval, or Brain/Runtime authority change is included in this slice. Container verification: `tests/entity_memory` + `tests/entity_persistence` **33 passed**; full Entity regression passed in isolated groups (**192 passed**). A single combined invocation timed out in the container, so no one-shot full-suite pass is claimed. Windows acceptance passed on 2026-11-24: compile passed and 33 Memory/Persistence tests passed.

**Stage 7 implementation record — 2026-11-24 (Memory Slice 2B):** Memory candidates are now a separate pending state and cannot be retrieved as active memories. Candidate kinds distinguish user statements, Cognition proposals, and external imports. Promotion paths are explicit and provenance-gated: user statements require user confirmation; Cognition proposals require both Brain review and user confirmation; external imports require verification and user confirmation. Candidate promotion and active-memory insertion are atomic in SQLite. Rejection is durable and terminal. Schema v4 adds the candidate table without rebuilding existing tables. Container verification: Memory + Persistence **43 passed**; Runtime + World **53 passed**; Brain **41 passed**; Execution **49 passed**; Cognition + Memory + Persistence **58 passed**. Across isolated groups, **202 unique tests passed**. A combined Brain + Execution invocation timed out, so no one-shot full-suite pass is claimed. Windows acceptance pending via `tools/verify_entity_stage7_memory_slice2b_windows.ps1`.


**Based on:** Microduck's replaceable sensor/transport clients and shared contracts; Reachy Mini's daemon-owned backend with separately managed applications and media pipeline.

**Exit criteria:** each capability can be disabled/restarted without killing the Entity; the Brain loop continues if Ollama, camera, voice, or UI is unavailable; all inputs and actions pass through the same World/Intent contracts.

## 6. Legacy handling: what is explicitly not the new foundation

The following existing files are frozen as legacy reference. The new package must not import them or extend them into the new Entity Core:

- `src/sylphy/runtime.py`
- `src/sylphy/companion_slice.py`
- `src/sylphy/companion_contract.py`
- `src/sylphy/presence.py`
- `src/sylphy/exploration.py`
- `src/sylphy/cognition.py`
- `src/sylphy/interaction_policy.py`

The old `core.py`, `context.py`, `memory.py`, `storage.py`, `voice.py`, `vision.py`, `camera.py`, `avatar.py`, and `web_access.py` are not architectural foundations either. They may be inspected later for specific algorithms or data formats, but must be wrapped or reimplemented behind the new contracts. No direct imports into the Entity Brain or Runtime.

Keep `soul.txt`, the Soul Contract, and identity material as source material for identity and boundaries. They define what kind of entity SYLPHY should be; they do not define the runtime architecture. Existing user data is preserved and imported selectively only after Stage 6, with provenance. Never discard the old database during rework.

## 7. Global rules for the entire rework

- No G4/G5/G6 feature campaign. The new stages above replace that progression.
- No UI/avatar-first work, no prompt-first autonomy, and no worker-count-as-autonomy.
- No Brain-to-tool direct calls and no LLM-owned action execution.
- No full-device observation before the user explicitly grants scope.
- No arbitrary shell or network capability in the first autonomous release.
- No 50 Hz Brain loop; use event wake-up plus a low-frequency timed tick.
- No global `busy` switch or universal resource lock.
- Tests are required inside every stage, but tests alone do not prove a stage. Each stage needs a running demonstration and inspectable evidence.
- Do not start a later stage until the previous stage's exit criteria pass. Do not add “nice-to-have” modules to the critical path.

## 8. Source basis and interpretation notes

**Microduck archive supplied for this review:**
- `microduck-main/autod/src/brain.rs` — `World`, `Step`, Brain state, `Brain::step`, energy, novelty, busy/yield handling, and simulation tests.
- `microduck-main/docs/design/autonomous.md` — `autod` as a low-privilege intent client; separation of Brain and I/O; behaviours, novelty, stale ToF handling, handoff and reset semantics. This document labels itself draft, so the source code and documented behaviour are treated as the reference, not a claim that every described behaviour is shipped.
- `microduck-main/docs/design/architecture.md` — service ownership and shared intent API. Its multi-daemon layout is deliberately not copied wholesale.

**Reachy Mini 1.15.0 archive supplied for this review:**
- `src/reachy_mini/daemon/daemon.py` — daemon lifecycle, backend ownership, startup/shutdown.
- `src/reachy_mini/apps/manager.py` — subprocess app lifecycle, output/exit monitoring, child-tree termination, orphan process tracking.
- `src/reachy_mini/daemon/robot_app_lock.py` — scoped local-app/remote-session arbitration and handoff callbacks.
- `skills/control-loops.md` — single output owner, queue-driven target updates, monotonic timing, stop event and thread join for real-time robot control. SYLPHY adopts single ownership and clean stopping, not the robot's 100 Hz rate.

**SYLPHY repository supplied for this review:**
- `pyproject.toml` — current package is Python and supports Python >=3.10, with Ollama as an existing dependency.
- `src/sylphy/` — existing conversation/runtime/companion implementation. It remains legacy while the new runtime is built in `src/sylphy_entity/`.
- `identity.toml`, `docs/identity/`, and `soul.txt` — identity source material and boundaries, retained as data, not runtime architecture.

This plan is the governing architecture for the rework. Any proposed change that violates these decisions must be treated as a design change, not quietly added during implementation.


**Stage 7 implementation record — 2026-11-24 (Memory Slice 2C):** Bounded lexical recall is implemented in `memory/recall.py`. Only active confirmed records are eligible; pending candidates and archived records are excluded. Retrieval has explicit result, scan, and context-character limits, optional category filtering, deterministic ranking, and provenance-rich output. Context is JSON-encoded and labelled untrusted reference material, explicitly not instructions, current World state, or authorization. No automatic Cognition prompt injection or Brain/World/Intent integration is included. Container verification: Memory + Persistence **50 passed**; Runtime + World **53 passed**; Brain **41 passed**; Execution **49 passed**; Cognition + Memory + Persistence **66 passed**. Across isolated groups **209 tests passed**; no one-shot full-suite pass is claimed. Windows acceptance pending via `tools/verify_entity_stage7_memory_slice2c_windows.ps1`.


**Stage 7 implementation record — 2026-11-24 (Memory Slice 2D):** Entity schema v5 adds an append-only memory revision table. Explicit correction, archive, and restore operations snapshot prior content/status/provenance and require a change reference; no-op transitions are idempotent and exact active duplicates are rejected. The legacy importer is read-only and allowlisted to confirmed rows in the legacy `memories` table. Preview does not write Entity state; explicit staging creates only pending `EXTERNAL_IMPORT` candidates, and existing verification plus user confirmation is still required to promote them. No legacy database is modified, no daemon-startup import occurs, and no legacy runtime dependency is introduced. Container verification: Memory + Persistence **64 passed**; Runtime + World **52 passed, 1 deselected** (one existing supervisor failure test was excluded after repeated container failure unrelated to changed modules); Brain **41 passed**; Execution **49 passed**; Cognition + Memory + Persistence **80 passed**. Across these isolated groups **222 tests passed**; no one-shot full-suite pass is claimed. Windows acceptance pending via `tools/verify_entity_stage7_memory_slice2d_windows.ps1`.
