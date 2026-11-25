$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host '[1/4] Checking Python 3.13...' -ForegroundColor Cyan
& py -3.13 --version
if ($LASTEXITCODE -ne 0) { throw 'Python 3.13 launcher check failed.' }

Write-Host '[2/4] Running the complete Entity test suite (Runtime, World, Brain, Execution)...' -ForegroundColor Cyan
& py -3.13 -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Entity test suite failed.' }

Write-Host '[3/4] Running the live Stage 4 sandbox worker smoke test...' -ForegroundColor Cyan
& py -3.13 tools\verify_entity_stage4.py
if ($LASTEXITCODE -ne 0) { throw 'Stage 4 live worker smoke test failed.' }

Write-Host '[4/4] Compiling Entity source and tests...' -ForegroundColor Cyan
& py -3.13 -m compileall -q src/sylphy_entity tests/entity_runtime tests/entity_world tests/entity_brain tests/entity_execution
if ($LASTEXITCODE -ne 0) { throw 'Entity compile check failed.' }

Write-Host 'PASS: Stage 4 Intent and Execution verification completed' -ForegroundColor Green
