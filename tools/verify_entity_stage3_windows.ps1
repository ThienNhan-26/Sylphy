$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

Write-Host '[1/3] Checking Python 3.13...' -ForegroundColor Cyan
& py -3.13 --version
if ($LASTEXITCODE -ne 0) { throw 'Python 3.13 launcher check failed.' }

Write-Host '[2/3] Running the complete Entity test suite...' -ForegroundColor Cyan
& py -3.13 -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Entity test suite failed.' }

Write-Host '[3/3] Compiling Entity source and tests...' -ForegroundColor Cyan
& py -3.13 -m compileall -q src/sylphy_entity tests/entity_runtime tests/entity_world tests/entity_brain
if ($LASTEXITCODE -ne 0) { throw 'Entity compile check failed.' }

Write-Host 'PASS: Stage 3 Brain verification completed' -ForegroundColor Green
