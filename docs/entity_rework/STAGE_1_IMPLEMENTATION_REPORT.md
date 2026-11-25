# Stage 1 Implementation Report — Entity Daemon & Runtime Authority

**Status:** COMPLETE — implemented, development-container tests passed, and Windows runtime smoke test accepted on 2026-11-23.

## Delivered source

### New package: `src/sylphy_entity/`

| File | Responsibility |
|---|---|
| `daemon.py` | Daemon process entry point |
| `runtime/supervisor.py` | Sole lifecycle authority and startup/shutdown orchestration |
| `runtime/lifecycle.py` | Explicit lifecycle state machine and legal transitions |
| `runtime/instance_lock.py` | Cross-platform OS lock preventing duplicate daemons |
| `runtime/event_loop.py` | Event wake-up plus monotonic 1 Hz timed tick |
| `runtime/process_manager.py` | argv-only subprocess start/monitor/stop, PID + creation-time identity, output capture |
| `runtime/paths.py` | Per-user runtime directory resolution |
| `contracts/events.py` | Immutable, timestamped event envelope |
| `interface/local_api.py` | Loopback health/status/event/shutdown API with local control token |
| `interface/cli.py` | Separate client process for status, event publication and graceful stop |
| `persistence/runtime_store.py` | SQLite WAL boot counter and previous clean-shutdown marker |

### Integration changes

- `pyproject.toml`: added FastAPI, Pydantic v2, Uvicorn and psutil as the new runtime's core dependencies; moved Ollama/Pillow/tomli to the optional `legacy` extra so Stage 1 does not require the old Companion stack. Added `sylphy-entity` and `sylphy-entityctl` console scripts.
- `README.md`: labels the old Companion documentation frozen and points to the active rework.
- `INSTALL_WINDOWS.md`: explicitly labels the old installation instructions as legacy.
- `docs/entity_rework/MASTER_PLAN.md`: copy of the controlling architecture plan.
- `docs/entity_rework/STAGE_1_RUNTIME.md`: installation, operation, boundaries and verification.
- `tools/verify_entity_stage1_windows.ps1`: target-machine smoke-test script.

## Design relationship to the reference projects

- **Reachy Mini:** one Daemon owns lifecycle; subprocess workers are monitored independently; PID is paired with process creation time rather than trusted alone; clean shutdown owns resource cleanup.
- **Microduck:** the Entity loop is separate from any client/presentation process. The loop only schedules and accepts events; it does not make behaviour decisions. Brain decision logic will be introduced in Stage 3, not hidden in the Supervisor.

No robot-specific motor/media daemon topology was copied. The runtime is one local modular process with isolated child workers where needed.

## Verification performed

- `python -m compileall -q src tests/entity_runtime` — passed.
- `PYTHONPATH=src pytest -q tests/entity_runtime` — **12 passed**.
- Editable installation with `pip install -e . --no-deps --no-build-isolation` — passed in the container.
- Both console entry points (`sylphy-entity`, `sylphy-entityctl`) — help and invocation verified.
- Live process smoke test — Daemon started independently; client CLI queried status and published an event; a second Daemon was refused with exit code 2; authenticated client shutdown stopped the Daemon; SQLite recorded `clean_shutdown=1`.
- Static architecture test confirms no import from `sylphy` inside `sylphy_entity`.

The test runner reports two upstream deprecation warnings from `websockets.legacy` / Uvicorn's websocket protocol import; they did not affect tests. The full frozen legacy suite was also attempted, but 20 test modules failed collection because the container does not have the optional `ollama` package installed; those old tests require the `.[legacy,dev]` extra. The active Entity tests pass independently. Ruff is not installed in the container, so linting with Ruff has not been claimed.

### Windows acceptance — passed 2026-11-23

The user ran `tools/verify_entity_stage1_windows.ps1` from the Windows repository. The terminal output ended with `PASS: Stage 1 Windows runtime smoke test completed`. The eight-step script checks:

1. Python 3.13 launcher availability.
2. Detached Daemon startup and health.
3. Independent CLI inspection and loopback-only API binding.
4. Event publication and processing.
5. Second Daemon refusal (exit code 2) while the original remains healthy.
6. Authenticated graceful shutdown and process exit.
7. Restart with SQLite `previous_shutdown_clean = true` and incremented boot count.
8. Final graceful shutdown.

This closes the previously pending Windows-specific acceptance item. It does not claim that Stage 2 World observation, Brain decisions, autonomous actions, or long-term goal continuity exist; those remain later stages.


## Post-acceptance reliability hardening (2026-11-23)

During Stage 3 regression testing, the worker-failure integration test exposed a race between a secondary worker observer and Daemon shutdown. The ProcessManager now notifies the lifecycle owner directly from its authoritative process monitor callback. `wait()` shields that monitor from cancellation of an individual waiter, so a cancelled/timed-out caller cannot cancel the only task that collects the subprocess result. The regression test also verifies callback delivery exactly once and that cancelling a waiter leaves the monitor alive. The complete worker-failure/Daemon lifecycle scenario passed 8/8 separate repeated runs after the change. This is reliability hardening of Stage 1's Runtime Authority; it does not add Brain or execution policy.

## Explicitly not implemented yet

- Filesystem sensor, watchdog observer, World Model or Observation projection (Stage 2).
- Brain, behaviour state machine, drives, goals or novelty (Stage 3).
- Intent authorization, resource arbitration or capability execution policy (Stage 4).
- Durable event/observation/goal/intent history and crash reconciliation (Stage 5).
- Autonomous closed loop or any LLM/Memory/Voice/Vision integration (Stages 6–7).

The current SQLite table stores only daemon boot metadata. Event ingress is transient. The 1 Hz tick is intentionally inert; there is no fake or prompt-driven Brain hidden in the runtime.

## Target-machine verification status

The Windows-specific Stage 1 verification is complete. No further user-side action is required for Stage 1. Windows validation for the filesystem adapter and sandbox observation will be requested as part of Stage 2, because those behaviours cannot be proven by the Linux development container.
