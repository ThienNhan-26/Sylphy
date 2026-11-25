# Entity Rework — Stage 1: Daemon and Runtime Authority

## Scope

This implementation is the first isolated runtime package: `src/sylphy_entity/`.
It does not import `src/sylphy/` and deliberately contains no Companion Slice,
Presence, Exploration, Memory, or LLM dependency.

## What is implemented

- One explicit Entity lifecycle state machine.
- A cross-platform OS-level single-instance lock.
- A minimal SQLite WAL boot record that detects whether the previous daemon
  shut down cleanly. It does not yet persist Brain, Goal, Intent, or World state.
- One event-driven asyncio loop, awakened by input events and ticking at 1 Hz
  by default. The tick callback is deliberately inert until the Brain stage.
- A loopback-only FastAPI interface (`127.0.0.1`) with health/status, event
  ingress, and graceful shutdown.
- A per-user control token protects event publication and shutdown requests.
- A subprocess manager that launches with argv (never a shell), records PID and
  process creation time, captures output, stops process trees, and contains
  worker failure so it degrades rather than terminates the daemon.
- Separate daemon and client commands: `sylphy-entity` and `sylphy-entityctl`.
  Closing the client does not terminate the Daemon.

## Install

Use the existing Python 3.13 environment on Windows:

```powershell
py -3.13 -m pip install -e ".[dev]"
```

## Start the Daemon in the background

```powershell
$daemonExe = (Get-Command sylphy-entity).Source
Start-Process -FilePath $daemonExe -ArgumentList @('--port', '8765') -WindowStyle Hidden
```

The Daemon owns its own process and runtime directory under
`%LOCALAPPDATA%\SYLPHY\EntityRuntime`. Its lock, control token, and SQLite WAL
metadata are separate from the old SYLPHY conversation database.

## Inspect from a separate CLI process

```powershell
sylphy-entityctl status
Invoke-RestMethod http://127.0.0.1:8765/health
```

Publish a test event (event ingress only; this does not execute actions):

```powershell
sylphy-entityctl publish --type manual.test --source operator --payload-json '{"note":"runtime check"}'
```

Gracefully stop the Daemon:

```powershell
sylphy-entityctl stop
```

The client reads the control token from the per-user runtime directory. The
API is bound to loopback and is not intended to be exposed to a LAN.

## Verify

```powershell
py -3.13 -m pytest tests/entity_runtime -q
```

## Windows acceptance status

**Accepted on 2026-11-23.** The Windows smoke-test script completed all eight checks, including detached startup, event ingress, single-instance refusal, graceful shutdown, and clean-shutdown/boot-count persistence after restart. The Stage 1 Windows-specific acceptance item is closed.

## Boundaries

This stage proves lifecycle ownership, scheduling, event ingress, local status,
single-instance exclusion, clean shutdown, boot continuity metadata, and worker
failure containment. It does **not** yet implement World Model, Brain, Goals,
Intent authorization, filesystem observation, persistent Entity continuity, or
autonomous behaviour. Those must be introduced in their dedicated stages; no
placeholder Brain decisions are made here.
