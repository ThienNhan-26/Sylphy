$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$pythonLauncher = Get-Command py -ErrorAction SilentlyContinue
if (-not $pythonLauncher) {
    throw "Python launcher 'py' was not found. Install Python 3.13 and the Windows py launcher."
}

& py -3.13 -c "import sys; print('Python', sys.version)"
if ($LASTEXITCODE -ne 0) {
    throw "Python 3.13 is required for Stage 5 verification."
}

# Allow imports directly from this overlay without reinstalling the package.
$env:PYTHONPATH = Join-Path $repoRoot "src"

Write-Host "[1/3] Compiling Entity source and tests..." -ForegroundColor Cyan
& py -3.13 -m compileall -q src/sylphy_entity tests
if ($LASTEXITCODE -ne 0) { throw "Entity compile check failed." }
Write-Host "PASS: Entity compile check completed" -ForegroundColor Green

Write-Host "[2/3] Running the complete Entity test suite..." -ForegroundColor Cyan
& py -3.13 -m pytest -q
if ($LASTEXITCODE -ne 0) { throw "Entity test suite failed." }

Write-Host "[3/3] Running Stage 5 persistence and crash-recovery process smoke test..." -ForegroundColor Cyan
& py -3.13 tools/verify_entity_stage5_runtime.py
if ($LASTEXITCODE -ne 0) { throw "Stage 5 process-level recovery verification failed." }

Write-Host "PASS: Stage 5 Persistent Entity State and Crash Recovery verification completed" -ForegroundColor Green
