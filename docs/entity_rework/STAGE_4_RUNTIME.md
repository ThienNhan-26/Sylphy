# SYLPHY Entity — Stage 4 Windows Acceptance Guide

This guide verifies the Stage 4 Intent/Execution boundary on the Windows development machine. It does not start autonomous operation or connect the dispatcher to the Daemon tick.

## Apply the overlay

1. Close any running SYLPHY Entity Daemon.
2. Back up the repository folder.
3. Extract `SYLPHY_ENTITY_REWORK_STAGE4_COMPLETE_OVERLAY.zip` into the repository root, for example:

   `D:\CWDI\SYLPHY_Sprint_10_plus_1_fixed\sylphy_work`

4. Allow replacement of the files included in the overlay. The archive does not contain user SQLite databases, memory data, logs, `.env` files, model files, or the legacy `src/sylphy` implementation.

## Run acceptance

Open PowerShell in the repository root and run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\tools\verify_entity_stage4_windows.ps1
```

The script performs:

1. Python 3.13 launcher check.
2. Full default Entity test suite (Runtime, World, Brain, Execution).
3. Live Stage 4 worker smoke test using a temporary sandbox. It checks metadata inspection, bounded text read, content hash evidence, worker PID/process-creation-time identity, mismatched scope rejection, revoked permission rejection, and worker cleanup.
4. Compile check for the Entity source and tests.

Expected final line:

```text
PASS: Stage 4 Intent and Execution verification completed
```

## Important boundaries

- The test uses a temporary directory and removes it automatically after all workers have exited.
- Only metadata inspection and bounded text reading are executed.
- No file is modified or deleted by the worker.
- No shell or network capability is enabled.
- The Brain is not connected to the autonomous Daemon loop.

If the script stops, send the complete PowerShell output. Do not proceed to Stage 5 until the Windows acceptance result is reviewed.
