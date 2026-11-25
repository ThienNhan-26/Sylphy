$ErrorActionPreference = 'Stop'
$Root = (Get-Location).Path
if (-not (Test-Path (Join-Path $Root 'pyproject.toml'))) {
    throw 'Run this script from the sylphy_work repository root.'
}

# Pin verification to the Python version used throughout the rework. Do not
# silently fall back to a different python.exe on PATH (e.g. Python 3.15).
$PyLauncher = Get-Command py -ErrorAction Stop
$VersionText = & $PyLauncher.Source -3.13 --version
if ($LASTEXITCODE -ne 0 -or $VersionText -notmatch 'Python 3\.13\.') {
    throw "Python 3.13 is required for Stage 6 acceptance. Detected: $VersionText"
}
Write-Host "Using $VersionText" -ForegroundColor Gray

Write-Host '[1/3] Compiling Entity source and all Entity tests...' -ForegroundColor Cyan
& $PyLauncher.Source -3.13 -m compileall -q src/sylphy_entity tests/entity_runtime tests/entity_world tests/entity_brain tests/entity_execution tests/entity_persistence
if ($LASTEXITCODE -ne 0) { throw 'Entity compile check failed.' }
Write-Host 'PASS: Entity compile check completed' -ForegroundColor Green

Write-Host '[2/3] Running Stage 6 mandatory closed-loop scenario matrix...' -ForegroundColor Cyan
& $PyLauncher.Source -3.13 -m pytest -q `
    tests/entity_runtime/test_stage6_autonomous_closed_loop.py `
    tests/entity_runtime/test_stage6_simulator_closed_loop.py `
    tests/entity_runtime/test_stage6_scenario_matrix.py
if ($LASTEXITCODE -ne 0) { throw 'Stage 6 mandatory scenario matrix failed.' }
Write-Host 'PASS: Stage 6 simulator/live closed-loop scenario matrix completed' -ForegroundColor Green

Write-Host '[3/3] Running the complete Entity test suite (Stages 1–6)...' -ForegroundColor Cyan
& $PyLauncher.Source -3.13 -m pytest -q
if ($LASTEXITCODE -ne 0) { throw 'Complete Entity test suite failed.' }
Write-Host 'PASS: Stage 6 autonomous closed-loop Windows acceptance completed' -ForegroundColor Green
