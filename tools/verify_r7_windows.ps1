$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot

Write-Host "[1/5] Python 3.13 runtime..."
py -3.13 --version
if ($LASTEXITCODE -ne 0) { throw "Python 3.13 is required." }

Write-Host "[2/5] Compile Entity, CLI, Avatar service, and tests..."
py -3.13 -m compileall -q src/sylphy_entity tests/entity_runtime tests/entity_voice tests/entity_embodiment
if ($LASTEXITCODE -ne 0) { throw "Python compile check failed." }

Write-Host "[3/5] Run the complete non-integration Entity regression suite..."
py -3.13 -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Entity regression suite failed." }

Write-Host "[4/5] Run Avatar TypeScript tests and production build..."
Push-Location (Join-Path $RepoRoot "avatar")
npm test
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Avatar Vitest suite failed." }
npm run desktop:build
if ($LASTEXITCODE -ne 0) { Pop-Location; throw "Avatar production build failed." }
Pop-Location

Write-Host "[5/5] Verify Avatar process manager command is reachable..."
$env:PYTHONPATH = Join-Path $RepoRoot "src"
py -3.13 -m sylphy_entity.embodiment.avatar_service status
if ($LASTEXITCODE -ne 0) { throw "Avatar service status command failed." }

Write-Host "PASS: R7 automated Windows acceptance completed." -ForegroundColor Green
Write-Host "Manual acceptance still required: real speaker output, transparent desktop behavior, tray/shortcuts, Avatar crash/restart, and Entity independence."
