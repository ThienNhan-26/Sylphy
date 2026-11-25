# Stage 2 — World Interface Runtime Guide

## Install

Run from the `sylphy_work` repository root in PowerShell:

```powershell
py -3.13 -m pip install -e ".[dev,world]"
```

The `world` extra installs Watchdog. The core runtime has a polling-only reconciliation fallback, but the Stage 2 Windows acceptance script requires Watchdog to be active.

## Verify Stage 2

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\tools\verify_entity_stage2_windows.ps1
```

The script creates a temporary sandbox under `%TEMP%`, starts the Daemon with explicit authorization for that one directory, tests initial discovery, create, modify, rename and delete, then stops the Daemon and removes the test sandbox.

A successful run ends with:

```text
PASS: Stage 2 Windows filesystem smoke test completed
```

## Run with a user-approved sandbox

Create a directory dedicated to observation, then start the Entity Daemon with that exact directory:

```powershell
New-Item -ItemType Directory -Force -Path "D:\SYLPHY-Sandbox"
sylphy-entity --sandbox-root "D:\SYLPHY-Sandbox"
```

From another PowerShell window:

```powershell
sylphy-entityctl status
sylphy-entityctl world
sylphy-entityctl stop
```

Without `--sandbox-root`, no filesystem adapter is started. The adapter observes metadata only (relative path, size and modified timestamp). It does not open or read document contents, index the full drive, follow symlinks/reparse points outside the sandbox, or perform actions. The World Model is currently in memory; durable observations and replay are Stage 5 work.
