# Stage 5 — Windows Runtime Verification Guide

This is the Windows acceptance step for **Stage 5 — Persistent Entity State and Crash Recovery**. It is not a new roadmap stage.

## 1. Before applying the overlay

Close any running SYLPHY Entity Daemon. Keep the original repository and its database. Make a backup of the working repository before replacing files.

Canonical repository path used in this project:

```text
D:\CWDI\SYLPHY_Sprint_10_plus_1_fixed\sylphy_work
```

## 2. Apply the overlay

Extract `SYLPHY_ENTITY_REWORK_STAGE5_COMPLETE_OVERLAY.zip` into the **repository root** (`sylphy_work`), not into `src` and not into the parent `SYLPHY_Sprint_10_plus_1_fixed` directory. Allow updated source, test, configuration and `tools` files to be replaced.

The overlay does not include the user's SQLite database, memory, logs, model files or `.env` files.

## 3. Run the acceptance script

Open PowerShell at the repository root and run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\tools\verify_entity_stage5_windows.ps1
```

The script:

1. Checks Python 3.13.
2. Compiles Entity source and tests.
3. Runs the full default Entity suite.
4. Runs a process-level recovery smoke test using a temporary SQLite database and a real child process.

No Ollama server or model is needed. The test uses a temporary directory and does not modify the existing SYLPHY database.

## 4. Expected output

The full test count may vary if tests are added later; the implementation currently reports **145 passed** in the development container. The process-level smoke should include:

```text
PASS: Entity identity, Brain drives, Goal and Novelty survived database restart
PASS: World Snapshot restored and downgraded to STALE pending re-observation
PASS: in-flight Intent classified UNKNOWN; verified orphan process terminated
PASS: Intent, permission revocation and worker recovery status persisted
PASS: Stage 5 persistence and crash-recovery runtime verification completed
PASS: Stage 5 Persistent Entity State and Crash Recovery verification completed
```

The script exits non-zero and prints the failing operation if any check fails.

## 5. What to send back

Send the complete PowerShell output, including the Python version, test summary, all smoke-test PASS lines and the final Stage 5 PASS line. If there is a failure, send the full red error block; do not manually edit the database or retry a partially completed operation before reviewing the error.

## 6. Stage boundary

Windows acceptance passed on 2026-11-24: 143 passed, 2 skipped, compile and process-level crash-recovery checks passed. Stage 5 is **COMPLETE — Windows verified**. Stage 6 is the current stage; Stage 7 remains locked until Stage 6 is accepted.
