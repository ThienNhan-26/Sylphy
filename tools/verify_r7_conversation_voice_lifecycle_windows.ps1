$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[1/3] Checking Python 3.13..."
py -3.13 --version
if ($LASTEXITCODE -ne 0) { throw "Python 3.13 is required." }

Write-Host "[2/3] Compiling Conversation Runtime and Voice lifecycle modules..."
py -3.13 -m compileall -q src/sylphy_entity/interaction src/sylphy_entity/voice tests/entity_conversation
if ($LASTEXITCODE -ne 0) { throw "Conversation/Voice compile check failed." }

Write-Host "[3/3] Running Conversation, Voice, and shutdown lifecycle regressions..."
py -3.13 -m pytest -q tests/entity_conversation tests/entity_voice tests/entity_runtime/test_supervisor.py
if ($LASTEXITCODE -ne 0) { throw "Conversation/Voice lifecycle regression failed." }

Write-Host "PASS: R7 Conversation/Voice lifecycle acceptance completed" -ForegroundColor Green
Write-Host "Note: this script does not start/stop the user's live Entity or Avatar."
