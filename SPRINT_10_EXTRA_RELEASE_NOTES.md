# SYLPHY sprint handoff — 10 + 1 extra

## Changes in this workspace

- Context history selection continues past a history message that exceeds the remaining budget.
- Memory retrieval failures are isolated from conversation context assembly and logged.
- Memory retrieval scoring uses tokenized terms, exact token matches, category matches, coverage, and phrase relevance.
- Retrieval query includes recent user context, bounded in size.
- Runtime worker start/stop lifecycle is synchronized and duplicate start is guarded.
- Worker restart/recovery behavior is covered by regression tests.
- Successful task execution clears stale worker error state.
- Avatar presentation state is validated/normalized at the runtime boundary.

## Research provenance

- **Mark VII:** examined task persistence/checkpoint and recall/retrieval patterns. SYLPHY changes adapt the principles to its existing SQLite and worker architecture; they do not claim to port Mark VII's task engine.
- **Animama:** examined emotion-state validation, memory separation, and avatar/runtime controller boundaries. SYLPHY's current change is presentation-state validation only. No new emotion engine, gesture engine, or VRM avatar system is claimed.

## Validation

- Unit test suite: 314 passed, 1 deselected in the development environment.
- `compileall`: passed.
- The `ollama` Python package was unavailable in the development environment; a temporary import shim was used for test collection. It did not simulate model inference.
- Live Ollama, Windows deployment, camera, voice, and avatar asset integration are not verified.

## Target-machine status

This archive is not installed on the user's Windows machine. Extract it, back up existing data, follow `INSTALL_WINDOWS.md`, and validate the configured local model before replacing the existing installation.
