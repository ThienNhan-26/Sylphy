# Stage 6 — First Autonomous Closed Loop (Windows Guide)

## 1. Apply the overlay safely

1. Stop any running SYLPHY Entity Daemon.
2. Back up the current `sylphy_work` repository.
3. Extract `SYLPHY_ENTITY_REWORK_STAGE6_COMPLETE_OVERLAY.zip` into the repository root:

   `D:\CWDI\SYLPHY_Sprint_10_plus_1_fixed\sylphy_work`

4. Allow the files included in the overlay to be replaced.
5. **Do not delete or replace the Stage-5 Entity SQLite database.** The overlay contains source, tests, tools and documentation only, not user database, memory, log, model or `.env` files.

## 2. Run expanded Windows acceptance

Open PowerShell at the repository root and run:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\tools\verify_entity_stage6_windows.ps1
```

The script explicitly selects Python 3.13 via `py -3.13`; it does not use whichever `python.exe` happens to be first on `PATH`.

It performs:
1. Compile check for Entity source and all Entity test packages.
2. Stage 6 focused live/simulator/scenario-matrix tests.
3. Full default Entity suite for Stages 1–6.

The expected test counts can increase with later regression coverage. The required final line is:

```text
PASS: Stage 6 autonomous closed-loop Windows acceptance completed
```

Two upstream WebSockets/Uvicorn deprecation warnings are non-failing unless the process exits non-zero.

## 3. Run the first live autonomous proof

Use a dedicated disposable directory containing test files only. Do not point autonomy at the whole repository, user profile, a folder with secrets, or important documents.

```powershell
New-Item -ItemType Directory -Force .\stage6_sandbox | Out-Null
Set-Content .\stage6_sandbox\proof.txt 'Stage 6 test file'
py -3.13 -m sylphy_entity.daemon --sandbox-root .\stage6_sandbox --autonomy --verbose
```

The Daemon must be explicitly started with `--autonomy` and `--sandbox-root`. Observation alone does not grant execution permission.

The first capability is `inspect_file_metadata` only. It inspects metadata and produces evidence; it does not read file contents, edit/delete files, execute shell commands or access the network.

## 4. Inspect status and notices

From another PowerShell window:

```powershell
py -3.13 -m sylphy_entity.interface.cli status
```

The local `/status` response exposes autonomy enabled/paused state, bounded history/notices, Brain behaviour/tick/active Goals, World and adapter status. History and notices retain the source Observation ID, reason, Intent status and evidence identifiers.

## 5. Pause and resume

The control token is stored in the per-user runtime directory as `control.token`. Use the runtime directory path reported by the daemon/Stage-1 guide if it differs from the example below.

```powershell
$token = Get-Content (Join-Path $env:LOCALAPPDATA 'SYLPHY\Entity\control.token') -Raw
$headers = @{ 'X-Sylphy-Control-Token' = $token.Trim() }
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/autonomy/pause' -Headers $headers
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/autonomy/resume' -Headers $headers
```

Pause stops autonomous admission and cancels eligible active autonomous work while observation can continue. Resume does not enable autonomy unless the Daemon was originally started with explicit opt-in and a sandbox.

## 6. Revoke/restore sandbox permission

The authenticated local endpoints revoke or restore the configured sandbox scope. Use them only in the disposable test sandbox:

```powershell
$token = Get-Content (Join-Path $env:LOCALAPPDATA 'SYLPHY\Entity\control.token') -Raw
$headers = @{ 'X-Sylphy-Control-Token' = $token.Trim() }
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/permissions/revoke' -Headers $headers -ContentType 'application/json' -Body '{}'
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8765/permissions/restore' -Headers $headers -ContentType 'application/json' -Body '{}'
```

## Safety and scope

- Autonomy is opt-in and sandbox-scoped.
- Only the registered read-only `inspect_file_metadata` capability is used for this Stage 6 proof.
- No arbitrary shell, unrestricted filesystem, content extraction, editing, deletion or network actions.
- Stage 6 does not integrate Ollama, Conversation, long-term Memory, Vision, Voice or Avatar.
- Keep the legacy `src/sylphy/` implementation frozen; the Entity code lives under `src/sylphy_entity/`.

## Current acceptance status

Container verification: Stage 6 focused suite **16 passed**, full Entity suite **161 passed**, compile PASS. Expanded Windows acceptance is pending. Stage 6 must not be marked accepted until the Windows script completes successfully. Stage 7 remains LOCKED.
