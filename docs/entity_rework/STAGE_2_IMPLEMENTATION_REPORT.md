# Stage 2 Implementation Report — World Interface, Observation & World Model

**Status:** COMPLETE — development-container tests and Windows filesystem smoke test accepted on 2026-11-23.

## Delivered

- `contracts/observation.py`: immutable Pydantic observation contract with provenance, timestamps, sequence, verification, permission scope and freshness.
- `contracts/world.py`: immutable `WorldFact` and `WorldSnapshot`; explicit `KNOWN`, `UNKNOWN` and `STALE` states.
- `world/model.py` and `world/projector.py`: in-memory authoritative projection, bounded observation-ID deduplication, unchanged-fact suppression, rename/deletion/inaccessibility semantics.
- `world/adapters/filesystem.py`: read-only adapter for exactly one explicitly configured sandbox; watchdog event hints plus periodic reconciliation; safe path containment; symlink/reparse-point exclusion; metadata-only observation (no file content reads); rename identification by watcher hints or stable file identity; inaccessible subtrees transition to `STALE` once and return to `KNOWN` only after re-observation.
- `world/adapters/simulated.py`: deterministic in-memory filesystem adapter emitting the same Observation contract and event kinds.
- Entity Daemon integration: `--sandbox-root`, configurable reconciliation interval, world observations passed through the existing event ingress, `/world` endpoint and `sylphy-entityctl world`.
- Optional dependency extra: `.[world]` installs watchdog; core daemon can run a polling-only fallback when watchdog is unavailable.

## Design boundaries

- Adapters publish observations only; they cannot call Brain or execute actions. External API clients cannot inject reserved `world.*` or `runtime.*` event types.
- The adapter does not read file contents, index the full disk, follow symlinks, or observe outside the configured root.
- No Brain, goal selection, action execution or LLM integration was added.
- World Model is in-memory in this stage. Durable event history and world replay remain Stage 5.
- System/process observation adapter is not included; Stage 2's first live source remains the permitted sandbox filesystem as required by the Master Plan.

## Development verification

- `python -m compileall -q src/sylphy_entity tests/entity_world` — passed.
- `PYTHONPATH=src pytest -q tests/entity_world tests/entity_runtime` — **27 passed**.
- Container has no network access and watchdog is not installed. The polling reconciliation fallback was exercised here; the actual watchdog Observer backend remains to be verified on Windows.

## Windows acceptance — passed 2026-11-23

The user ran `tools/verify_entity_stage2_windows.ps1` on Windows after installing `.[dev,world]`. The terminal displayed `PASS: Stage 2 Windows filesystem smoke test completed`. The script reached its final marker after filesystem observation checks and Daemon shutdown. Windows refused to remove its temporary sandbox during cleanup and emitted a warning; this did not fail the smoke test or leave a live Daemon. The disposable directory can be removed manually after confirming no SYLPHY Entity process is running.

Stage 2 is accepted. The cleanup warning is recorded as a test-harness limitation, not as an observation/World Model failure. The adapter itself remains sandbox-scoped and read-only.
