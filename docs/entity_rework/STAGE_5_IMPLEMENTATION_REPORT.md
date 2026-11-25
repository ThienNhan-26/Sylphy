# SYLPHY Entity Rework — Stage 5 Implementation Report

**Stage:** 5 — Persistent Entity State and Crash Recovery  
**Status:** COMPLETE — Windows verified (2026-11-24)  
**Governing plan:** `SYLPHY_ENTITY_REWORK_MASTER_PLAN.md` (seven-stage plan; no renumbering)

## 1. Stage 5 objective

Make continuity belong to the Entity rather than the UI/session. A restart must restore durable state, reconcile interrupted work conservatively, re-observe the world and avoid repeating a side effect whose outcome is uncertain.

Stage 5 does **not** attach the Brain to the Daemon decision loop, build the end-to-end autonomous scenario, or integrate LLM/Memory/Conversation/Vision/Voice/Avatar. Those belong to Stages 6 and 7.

## 2. Implemented capabilities

### 2.1 One SQLite/WAL persistence owner

`src/sylphy_entity/persistence/database.py` introduces `EntityDatabase`, a single SQLite connection with:

- WAL mode, `synchronous=FULL`, foreign keys and busy timeout.
- Serialized transactions through one connection and lock.
- Schema versioning and migration.
- Adoption of the Stage 1 `daemon_runtime` database without discarding boot history.
- v1→v2 migration adding the observation `projected` marker used by crash-safe World projection.

The existing `RuntimeStore` uses the same database rather than opening a competing persistence file/connection.

### 2.2 Durable event and observation journal

`EventStore` provides append-only event sequencing, stable event identity, event replay and Observation provenance. Reusing an event or observation ID with different content is rejected.

Observation rows have a `projected` flag. The observation provenance insert is committed before projection; the World Snapshot and setting the `projected` flag are committed together. If the process stops between those operations, the event can be replayed on the next startup rather than silently lost.

### 2.3 Persistent World and Brain state

`StateRepository` stores:

- Stable Entity ID.
- Checksummed Brain/Entity snapshots: behaviour, virtual time, five drives, goals, novelty history, tick metadata and decision state.
- Checksummed World snapshots with the event sequence checkpoint.
- Goal records and permission grants/revocations.

Transient latches and an old `YIELDING` runtime condition are not blindly restored as active external ownership. Suspended goals are restored to their safe resume state without charging offline time. Restored World facts are marked `STALE` until adapters re-observe them.

### 2.4 Durable Intent, execution and resource history

Before a capability handler can create a side effect:

1. Intent acceptance is journaled.
2. Resource leases are persisted.
3. The Intent is moved to `DISPATCHING` in a committed transaction.
4. Only then can the handler/worker execute.

Execution results and evidence references are persisted. Idempotency fingerprints survive database close/reopen. A repeated completed Intent returns the previous evidence-backed result; an unresolved Intent returns `UNKNOWN` and is not redispatched. Monotonic timestamps are not treated as valid clocks across a process restart.

### 2.5 Worker identity and crash recovery

Worker records retain PID, process creation time, daemon instance ID, start/end time and terminal state. ProcessManager persists both worker start and exit (normal, failure, cancellation).

Recovery verifies both PID and creation time before terminating an orphan. PID alone is never trusted. A mismatched or unavailable creation time is not killed. Active leases from a prior daemon are marked `ABANDONED`.

Incomplete Intent recovery is conservative:

- `ACCEPTED` → `INTERRUPTED` (it had not durably crossed dispatch).
- `DISPATCHING` / `RUNNING` → `UNKNOWN` (a side effect may have happened).
- No automatic retry of uncertain execution.

## 3. Files changed/added

New persistence package:

```text
src/sylphy_entity/persistence/
├── database.py
├── codec.py
├── event_store.py
├── state_repository.py
├── recovery.py
└── runtime_store.py
```

Key integrations:

- `runtime/supervisor.py` uses one database, runs recovery before serving, replays pending events, restores state and persists worker lifecycle.
- `runtime/process_manager.py` has an awaited `on_started` callback before monitoring begins; it safely stops a child if durable identity recording fails.
- `world/model.py` restores snapshots and marks pre-restart facts stale.
- `execution/dispatcher.py` journals acceptance/dispatch/result and consults durable idempotency before side effects.
- `execution/permissions.py` persists sandbox permission changes.
- `pyproject.toml` includes persistence tests in the default Entity suite and defines pytest-asyncio fixture loop scope explicitly.

Added tests under `tests/entity_persistence/` for event store, schema migration, state restore/checksum, recovery, dispatcher journaling, idempotency and Daemon restart.

## 4. Verification evidence

Container verification on Python 3.13:

| Check | Result |
|---|---:|
| `python -m compileall -q src/sylphy_entity tests` | PASS |
| Full default Entity suite | **145 passed** |
| Actual worker-process orphan termination with PID+creation-time match | PASS |
| Wrong creation-time identity must not terminate process | PASS |
| `tools/verify_entity_stage5_runtime.py` | PASS |

The full suite reports two upstream deprecation warnings from `websockets.legacy` and Uvicorn's WebSocket protocol import. They are warnings, not test failures. Ruff and mypy are not installed in this container and were not run.

The Stage 5 runtime smoke test demonstrated:

- Entity ID remains unchanged across database reopen.
- Brain drives, active Goal and Novelty history restore.
- World snapshot is restored and old facts are marked `STALE`.
- An in-flight Intent is reconciled to `UNKNOWN`.
- A real orphan child process is terminated only after its PID and creation time match.
- Permission revocation and worker recovery status remain durable.

## 5. Windows acceptance evidence

The user ran `tools/verify_entity_stage5_windows.ps1` on Windows with Python 3.13.17. The observed output was:

- Compile check: PASS.
- Full Entity test suite: **143 passed, 2 skipped**.
- Stable Entity identity, Brain drives, Goal and Novelty survived database restart: PASS.
- World Snapshot restored and downgraded to `STALE` pending re-observation: PASS.
- In-flight Intent classified `UNKNOWN`; verified orphan process terminated: PASS.
- Intent, permission revocation and worker recovery status persisted: PASS.
- Final output: `PASS: Stage 5 Persistent Entity State and Crash Recovery verification completed`.

Stage 5 is therefore **COMPLETE — Windows verified**. Stage 6 may proceed under the Master Roadmap. Stage 7 remains locked until Stage 6 exit criteria pass.
