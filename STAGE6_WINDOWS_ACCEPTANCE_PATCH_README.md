# SYLPHY Stage 6 Windows Acceptance Fix

This overlay fixes two contract mismatches found by the Windows acceptance run.

## Files

- `src/sylphy_entity/world/adapters/simulated.py`
  - The simulated adapter now emits the configured `permission_scope` instead of hard-coding `simulated:sandbox`.
  - This lets the simulator use the same canonical sandbox scope as the execution PermissionPolicy when the full simulator-to-real-worker test is intentionally configured against a real temporary sandbox.

- `tests/entity_runtime/test_stage6_scenario_matrix.py`
  - The real child-daemon crash test now builds its Intent scope from `supervisor._permission_policy.scope`.
  - Its resource claim now matches the exact execution contract: `filesystem:<canonical-sandbox-scope>:<relative-path>`.
  - Therefore the Dispatcher authorizes the Intent, durably commits `DISPATCHING`, then calls the crash handler. The test still requires process exit code 73 and verifies restart recovery does not replay the Intent.

No permission checks are weakened. No legacy `src/sylphy/` code is changed. No Stage 7 work is included.

## Apply

Extract this ZIP into the repository root `sylphy_work`, allowing the two files to be overwritten.

## Verify (PowerShell, repository root)

```powershell
py -3.13 -m pytest `
  tests/entity_runtime/test_stage6_autonomous_closed_loop.py `
  tests/entity_runtime/test_stage6_simulator_closed_loop.py `
  tests/entity_runtime/test_stage6_scenario_matrix.py `
  -q

py -3.13 -m pytest -q
```

The Windows acceptance script can then be run:

```powershell
.\tools\verify_entity_stage6_windows.ps1
```

Container verification on the prepared source: Stage 6 acceptance 17 passed; full Entity suite 162 passed; compileall passed. Windows acceptance must still be rerun by the user after applying this overlay.
