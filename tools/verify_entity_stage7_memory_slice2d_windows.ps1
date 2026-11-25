$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[1/3] Checking Python 3.13..." -ForegroundColor Cyan
$python = py -3.13 --version
if ($LASTEXITCODE -ne 0) { throw "Python 3.13 is required." }
Write-Host $python -ForegroundColor Gray

Write-Host "[2/3] Compiling Entity memory lifecycle, correction, legacy import and persistence source/tests..." -ForegroundColor Cyan
py -3.13 -m compileall -q src/sylphy_entity tests/entity_memory tests/entity_persistence
if ($LASTEXITCODE -ne 0) { throw "Entity memory lifecycle compile check failed." }
Write-Host "PASS: Entity memory lifecycle compile check completed" -ForegroundColor Green

Write-Host "[3/3] Running Memory lifecycle/import and Persistence regression..." -ForegroundColor Cyan
py -3.13 -m pytest tests/entity_memory tests/entity_persistence -q
if ($LASTEXITCODE -ne 0) { throw "Entity memory lifecycle/persistence tests failed." }

Write-Host "PASS: Stage 7 Memory Slice 2D Windows acceptance completed" -ForegroundColor Green
