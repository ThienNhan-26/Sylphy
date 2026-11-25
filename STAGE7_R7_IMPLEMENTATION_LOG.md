# SYLPHY Stage 7 — R7 implementation log (working checkpoint)

## Scope implemented in this checkpoint

- Added a dedicated plain-text conversation generation interface, separate from structured `CognitionInterface`.
- Added a loopback-only Ollama conversation adapter (`OllamaConversationGenerator`) with a single inference slot, bounded context (maximum 24 messages), timeout, bounded output, and no tool/action interface.
- Added `ConversationRuntime`: durable user ingress through the existing `ConversationIngress` and `EventStore`, asynchronous generation outside the sequential Entity event callback, lifecycle-owned task cancellation, and completion/failure/interruption events.
- Integrated bounded confirmed-memory recall as explicitly untrusted reference context. Memory remains read-only in this flow; no candidate is silently promoted to confirmed memory.
- Added restart recovery: completed conversations are rebuilt from the existing event journal; in-flight generation is marked interrupted and is not replayed.
- Added authenticated `POST /chat`, `GET /conversation/history`, and conversation status in `/status`.
- Added `--conversation-model`; it is independent of `--autonomy` and `--sandbox-root`.
- Connected the existing Voice capability to Conversation Runtime when configured. Speech remains optional and text completion does not depend on it.
- Added duplicate-message idempotency so a retry with the same durable message ID does not start a second generation.

## Files changed by this checkpoint

- `src/sylphy_entity/interaction/generator.py` (new)
- `src/sylphy_entity/interaction/conversation_runtime.py` (new)
- `src/sylphy_entity/runtime/supervisor.py`
- `src/sylphy_entity/interface/local_api.py`
- `src/sylphy_entity/daemon.py`
- `tests/entity_conversation/test_conversation_runtime.py` (new)

## Verification

- `python -m compileall -q src/sylphy_entity` — PASS
- `PYTHONPATH=<repo>/src pytest -q` — 303 passed, 2 upstream deprecation warnings.
- Windows runtime, Ollama generation, speaker playback, and Avatar E2E are NOT yet claimed by this checkpoint. Those require Windows acceptance on the user's machine.

## Safety and compatibility notes

- No changes were made to legacy `src/sylphy/` in this checkpoint.
- No Brain or Runtime permission authority was delegated to the LLM.
- No new event bus, database, or memory store was introduced.
- Camera, microphone, STT, and emotion inference were not enabled.
- This is an implementation checkpoint, not Stage 7 completion. R7 continues with Voice/Entity synchronization, Avatar arbitration/runtime and Windows acceptance.

## R7 lifecycle hardening — Conversation-owned Voice tasks

- Conversation-generated speech tasks are now tracked separately from generation tasks.
- Conversation shutdown first cancels/drains generation, then cancels/drains any Voice playback tasks, before the supervisor stops Voice, EventLoop, and SQLite.
- Voice task failures are logged instead of silently discarded; text completion remains independent of optional speech playback.
- `/status` conversation projection now reports `active_voice_sessions` alongside active generations.
- Added a cancellation regression test proving no Conversation-created Voice task remains active after shutdown.
- Added `tools/verify_r7_conversation_voice_lifecycle_windows.ps1` for repeatable Python 3.13 focused acceptance on Windows; it does not touch the live daemon.

Verification after this change:

- `PYTHONPATH=<repo>/src pytest -q tests/entity_conversation tests/entity_embodiment` — 39 passed.
- `PYTHONPATH=<repo>/src pytest -q` — 304 passed, 2 upstream deprecation warnings.
- Avatar npm tests/build not run in this environment because `avatar/node_modules` is absent. Windows desktop and real speaker acceptance remain pending.

## R7 integration hardening — Entity → Avatar → Desktop presentation bridge

- Wired the existing typed `AvatarEvent` adapter and `BehaviorArbiter` into the Entity's authoritative event projection path. The Avatar receives only durable Entity facts; it cannot mutate Brain, Runtime, Memory, permissions, or Voice state.
- Added an immutable/read-only presentation snapshot projection from the arbiter through `GET /avatar/presentation`.
- Connected the Electron main process to that loopback read-only endpoint and exposed a narrow preload method. The renderer polls revisions and dispatches typed presentation frames to the existing VRM behavior runtime.
- Voice playback start/completion now drives the Avatar's speaking/idle presentation through the same durable Entity event path. Voice amplitude lip-sync remains driven by the playback-clock endpoint and does not take ownership of the Avatar behavior arbiter.
- Added `sylphy-avatar` as an installed CLI entry point for the independent Avatar process manager.
- Expanded `sylphy-entityctl` with `chat`, `history`, `speak`, `voice-stop`, and `avatar` commands; Entity remains the only lifecycle authority.
- Fixed sandbox subprocess import-root propagation so a worker launched with the sandbox as its working directory can import the checked-out `sylphy_entity` package.
- Added regression coverage for worker execution, Avatar event projection, read-only API presentation, defensive snapshot copies, and CLI actions.

Verification on the available Linux/Python 3.13 environment:

- `PYTHONPATH=src pytest -q` — 311 passed, 2 upstream WebSocket deprecation warnings; integration-marked Ollama test excluded by the repository's default pytest configuration.
- `python -m compileall -q src/sylphy_entity` — PASS.
- `node --check avatar/desktop/main.cjs` — PASS.
- `node --check avatar/desktop/preload.cjs` — PASS.
- Avatar `npm test` and production build were NOT run: npm dependencies could not be installed in this environment (offline cache miss and registry install timeout). No partial `node_modules` directory is retained.
- Windows speaker/device playback, Electron desktop behavior, tray/shortcut behavior, and process crash/restart acceptance remain unverified here and must be run on the target Windows machine with `tools/verify_r7_windows.ps1` plus the manual checks printed by that script.

This checkpoint is an R7 integration candidate, not a claim that physical Windows acceptance has passed.


## Final R7 one-pass completion update — 2026-11-25

Operator integration was completed:
- Added CLI inspection commands for Cognition, Memory, Voice and diagnostics.
- Added CLI controls for autonomy pause/resume and permission revoke/restore.
- Added Avatar presentation inspection and independent process start/stop/restart/status commands.
- Added bounded read-only `/memory/status` metadata/provenance endpoint.
- Added regression tests for CLI controls and memory inspection.
- Fixed a TypeScript temporal-dead-zone reference in `avatar/src/main.ts` by moving the `desktopBridge` declaration before the first use.

Final automated evidence in this environment: 319 Python tests passed in three groups; Python compileall and Electron main/preload syntax checks passed. npm install timed out; Vitest, Vite build, Windows speaker, and interactive desktop acceptance remain pending. This is an implementation-complete candidate, not a false claim of Windows acceptance.
