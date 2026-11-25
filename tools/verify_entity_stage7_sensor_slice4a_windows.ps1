$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[1/3] Checking Python 3.13..."
py -3.13 --version
if ($LASTEXITCODE -ne 0) { throw "Python 3.13 is required." }

Write-Host "[2/3] Compiling Entity sensor contracts and adapters..."
py -3.13 -m compileall -q src/sylphy_entity/contracts/sensor.py src/sylphy_entity/sensors tests/entity_sensors
if ($LASTEXITCODE -ne 0) { throw "Entity sensor compile check failed." }
Write-Host "PASS: Entity sensor compile check completed" -ForegroundColor Green

Write-Host "[3/3] Running sensor consent, freshness, privacy and resource regression..."
py -3.13 -m pytest -q tests/entity_sensors tests/entity_world/test_world_model.py tests/entity_execution/test_resource_arbiter.py
if ($LASTEXITCODE -ne 0) { throw "Sensor consent/resource regression failed." }

Write-Host "PASS: Stage 7 Sensor Slice 4A Windows acceptance completed" -ForegroundColor Green
