# SYLPHY Entity Rework — Stage 4 Implementation Report

**Status: IMPLEMENTATION COMPLETE IN WORKING COPY — CONTAINER VERIFIED; WINDOWS ACCEPTANCE PENDING.**

Updated: 2026-11-24

## Scope

Stage 4 implements the bounded pipeline:

`IntentProposal → IntentRouter → PermissionPolicy → ResourceArbiter → IntentDispatcher → isolated Worker → verified ExecutionResult`.

This stage does not connect autonomous execution to the Daemon tick. The live autonomous closed loop remains Stage 6. It does not persist Goals, Intents, executions, or leases across restarts; that belongs to Stage 5.

## Delivered

### Contracts and routing

- Immutable `Intent`, `ResourceRequirement`, `ResourceLease`, and `ExecutionResult` contracts.
- Contract validation for bounded priority/timeouts, monotonic deadlines, non-blank identifiers, unique scalar arguments, typed resources, timezone-aware result timestamps, immutable actual results, and evidence required for `SUCCEEDED`.
- `IntentRouter` accepts only the two registered sandbox capabilities, enforces matching permission class, exact sandbox-relative target, exactly one `relative_path` argument, and one path-scoped resource claim.
- Idempotency fingerprints include the semantic request fields, including `reason` and `priority`; reusing a key with changed request semantics is rejected.

### Permission boundary

- Capability allowlist contains only `inspect_file_metadata` and `read_sandbox_text`.
- Permission classes are limited to `OBSERVE_SANDBOX` and `READ_SANDBOX`.
- The target must be a regular file under the explicitly configured sandbox.
- Absolute paths, traversal, Windows drive/ADS syntax, symlinks, and reparse points are rejected.
- Read access is bounded by a configurable byte limit.
- Scope revocation prevents new authorization and stops interruptible active work; explicitly atomic work is allowed to reach its bounded completion boundary before revocation returns.
- No write, delete, arbitrary shell, or network capability is registered.

### Resource arbitration and takeover

- Shared/exclusive leases are acquired atomically per resource; independent resources do not share a global busy flag.
- Reusing an owner ID cannot bypass an incompatible exclusive lease.
- Leases are released on success, failure, timeout, and cancellation.
- User-origin work takes precedence over autonomous work. Admission is serialized so new autonomous work cannot slip between cancellation and user dispatch.
- `CANCEL_ON_TAKEOVER` work is cancelled; `FINISH_ATOMIC` work is drained to its explicit completion boundary. New autonomous work is blocked while user-origin execution remains active.
- Pause/resume and permission-scope revoke/restore are supported.

### Worker supervision and evidence

- Worker starts via argv with `asyncio.create_subprocess_exec`; no shell is invoked.
- `ProcessManager` captures stdout/stderr, return code, PID, process creation time, lifecycle state, cancellation, and cleanup.
- PID + creation time are used to validate descendants before process-tree operations. The direct child is stopped through its owned asyncio subprocess handle even when psutil cannot read creation time; this prevents an inaccessible creation-time lookup from leaving the child alive.
- The sandbox file worker revalidates the target and returns canonical SHA-256 evidence.
- The parent independently recomputes the canonical evidence digest, validates the exact response schema, checks file metadata types, and recomputes the text content hash and byte count before returning `SUCCEEDED`.
- Worker crash/invalid output/timeout are surfaced as non-success results with captured log evidence where available.

## Verification evidence (2026-11-24)

- `python -m pytest -q tests/entity_execution`: **49 passed**.
- `python -m pytest -q tests/entity_execution tests/entity_runtime/test_process_manager.py`: **55 passed** (execution boundary plus process-manager lifecycle regressions).
- Full default Entity suite: **119 passed, 2 upstream deprecation warnings**.
- `python tools/verify_entity_stage4.py`: **PASS**. It launched isolated worker processes, performed metadata inspection and bounded text read, rejected a mismatched scope, rejected a revoked scope, verified PID/creation-time identity and confirmed worker cleanup.
- `python -m compileall -q src/sylphy_entity tests/entity_runtime tests/entity_world tests/entity_brain tests/entity_execution`: **PASS**.
- Regression tests cover idempotency changes, same-owner lease bypass, atomic user takeover, autonomous admission during takeover, atomic pause and scope revocation, timeout/deadline, worker evidence tampering, and process stop when psutil creation time is inaccessible.

The two warnings are upstream deprecations from the installed `websockets.legacy` and Uvicorn websocket protocol imports. Ruff and mypy are not installed in this container; no lint or static type-check pass is claimed.

## Windows acceptance still required

The container is Linux and cannot verify the Windows subprocess/process-group behaviour or Windows reparse-point metadata. The Windows acceptance command is:

```powershell
.\tools\verify_entity_stage4_windows.ps1
```

That script runs the complete Entity test suite, the live Stage 4 worker smoke test, and compile checks. Windows acceptance was subsequently completed by the user. The Windows run reported 117 passed, 2 skipped, and ended with `PASS: Stage 4 Intent and Execution verification completed`.

```text
PASS: Stage 4 Intent and Execution verification completed
```

## Boundaries intentionally preserved

- Dispatcher is not wired into Daemon autonomous tick; autonomous live integration is Stage 6.
- Intent, execution results, and resource leases are not durable; Stage 5 owns persistence/reconciliation.
- The only live executor is read-only sandbox metadata/text inspection.
- No LLM, Memory, Conversation, Vision, Voice, Avatar, unrestricted filesystem, write/delete, shell, or network integration.
- No claim of an autonomous live Entity is made.

## Source files

- `src/sylphy_entity/contracts/execution.py`
- `src/sylphy_entity/execution/{intent_router,permissions,resource_arbiter,dispatcher,worker_executor}.py`
- `src/sylphy_entity/execution/workers/file_inspector.py`
- `src/sylphy_entity/runtime/process_manager.py`
- `tests/entity_execution/`
- `tests/entity_runtime/test_process_manager.py`
- `tools/verify_entity_stage4.py`
- `tools/verify_entity_stage4_windows.ps1`
