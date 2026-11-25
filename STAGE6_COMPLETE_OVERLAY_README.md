# SYLPHY Entity Rework — Stage 6 Complete Overlay

This is a source overlay for the existing `sylphy_work` repository, not a full repository archive.

## Apply

1. Stop the SYLPHY Entity Daemon.
2. Back up the repository folder.
3. Extract this ZIP into the repository root (`sylphy_work`) and allow replacement of the included files.
4. Do not remove or replace the Entity SQLite database, legacy databases, memory, logs, models or `.env` files.
5. Run `tools/verify_entity_stage6_windows.ps1` from the repository root.

The verification script explicitly selects Python 3.13 with `py -3.13`. It compiles Entity source/tests, runs the complete Stage 6 focused scenario matrix, then runs the complete default Entity test suite.

## Overlay boundaries

- Only `src/sylphy_entity/`, `tests/entity_runtime/`, `tools/`, and Stage 5/6 roadmap/report documentation are updated.
- `src/sylphy/` is not modified; it remains frozen legacy reference.
- No user databases, memories, logs, model files, secrets, `.env`, `.venv`, `node_modules`, build output or caches are included.
- Autonomy remains opt-in and requires an explicit sandbox root.
- The only autonomous worker capability in this proof is read-only file metadata inspection.

## Current evidence

- Stage 6 focused suite: 16 passed.
- Full Entity suite: 161 passed.
- Entity source and test compilation: passed.
- Two non-failing upstream deprecation warnings originate from WebSockets/Uvicorn.

Container verification is complete. Expanded Windows acceptance remains pending until the PowerShell script completes successfully on the user's machine.
