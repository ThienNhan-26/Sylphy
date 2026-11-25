# SYLPHY Stage 6 — Windows Acceptance Fix V3

This is a consolidated overlay for the current Stage 6 source. It includes the prior V2 fixes and the final UNKNOWN-target recovery fix.

## Changes

- Simulator observations retain the configured sandbox permission scope.
- The real-process crash test constructs the canonical filesystem resource claim from the configured PermissionPolicy.
- EventStore lists unresolved UNKNOWN autonomous metadata targets and records a durable `execution.unknown_target_superseded` event when later observation proves that the target is a new revision, deleted, or renamed.
- EntitySupervisor quarantines only that exact target after restart. It suppresses the first re-observed revision so it cannot be inspected again automatically.
- A persisted Brain Goal belonging to an UNKNOWN dispatch becomes `GoalStatus.UNKNOWN`, so it does not remain stuck ACTIVE or block unrelated goals. A later changed revision reopens that same Goal as a new attempt.
- The release survives subsequent daemon restarts.
- The crash-recovery acceptance test now checks same-revision suppression, changed-revision re-admission, and durable release across another restart.

PermissionPolicy remains strict. No additional capability, network, arbitrary shell, write, or delete permission has been introduced.

## Apply

1. Stop the Entity Daemon.
2. Back up `sylphy_work`.
3. Extract this ZIP into the `sylphy_work` repository root and allow the four files to be replaced.
4. Keep the existing Stage 5 SQLite database. This ZIP contains no database, memory, logs, model files, or `.env` files.

## Verify on Windows

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
py -3.13 -m pytest `
  tests/entity_runtime/test_stage6_autonomous_closed_loop.py `
  tests/entity_runtime/test_stage6_simulator_closed_loop.py `
  tests/entity_runtime/test_stage6_scenario_matrix.py `
  -q

.\tools\verify_entity_stage6_windows.ps1
```

Expected current counts: Stage 6 focused matrix **17 passed**; full Entity suite **162 passed**. Counts may increase as regression tests are added. The final Windows script line must be:

```text
PASS: Stage 6 autonomous closed-loop Windows acceptance completed
```

## Status

Container verification: compile PASS, Stage 6 focused suite 17/17 PASS, full Entity suite 162/162 PASS. Stage 6 remains open until this V3 overlay passes the expanded Windows acceptance on the user's machine. Stage 7 remains locked.
