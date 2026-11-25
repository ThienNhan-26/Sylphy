$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[1/3] Checking Python 3.13..."
py -3.13 --version
if ($LASTEXITCODE -ne 0) { throw "Python 3.13 is required." }

Write-Host "[2/3] Compiling Entity World/Runtime conversation projection source and tests..."
py -3.13 -m compileall -q src/sylphy_entity/world src/sylphy_entity/runtime/supervisor.py tests/entity_world tests/entity_runtime/test_supervisor.py
if ($LASTEXITCODE -ne 0) { throw "Conversation World projection compile check failed." }
Write-Host "PASS: Conversation World projection compile check completed" -ForegroundColor Green

Write-Host "[3/3] Running focused Conversation World projection and Runtime acceptance..."
py -3.13 -m pytest -q tests/entity_world/test_world_model.py tests/entity_conversation tests/entity_runtime/test_supervisor.py::test_conversation_event_is_projected_to_world_without_copying_message_text
if ($LASTEXITCODE -ne 0) { throw "Conversation World projection regression failed." }

Write-Host "PASS: Stage 7 Conversation Slice 3B Windows acceptance completed" -ForegroundColor Green
