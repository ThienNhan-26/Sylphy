$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[1/3] Checking Python 3.13..."
py -3.13 --version
if ($LASTEXITCODE -ne 0) { throw "Python 3.13 is required." }

Write-Host "[2/3] Compiling Entity conversation source/tests..."
py -3.13 -m compileall -q src/sylphy_entity/contracts/conversation.py src/sylphy_entity/interaction tests/entity_conversation
if ($LASTEXITCODE -ne 0) { throw "Entity conversation compile check failed." }
Write-Host "PASS: Entity conversation compile check completed" -ForegroundColor Green

Write-Host "[3/3] Running Conversation ingress and Event persistence regression..."
py -3.13 -m pytest -q tests/entity_conversation tests/entity_persistence/test_event_store.py
if ($LASTEXITCODE -ne 0) { throw "Conversation/Event persistence regression failed." }

Write-Host "PASS: Stage 7 Conversation Slice 3A Windows acceptance completed" -ForegroundColor Green
