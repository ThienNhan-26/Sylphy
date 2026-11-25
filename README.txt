Stage 6 Windows acceptance fixes

Contains two corrected files:
1. src/sylphy_entity/world/adapters/simulated.py
   - Adds permission_scope constructor argument and includes it in Observation.
2. tests/entity_runtime/test_stage6_scenario_matrix.py
   - Makes real-daemon crash-after-dispatch test deterministic by injecting a dispatcher
     and executing a constructed Intent after daemon startup. It no longer depends on
     filesystem watcher timing to trigger the crash callback.

Apply by extracting into the sylphy_work repository root and allowing overwrite.
Then run:
  py -3.13 -m pytest tests/entity_runtime/test_stage6_simulator_closed_loop.py tests/entity_runtime/test_stage6_scenario_matrix.py -q
  .\tools\verify_entity_stage6_windows.ps1

Do not declare Stage 6 Windows acceptance complete unless both commands pass.
