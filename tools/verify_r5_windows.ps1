$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Avatar = Join-Path $Root "avatar"

Write-Host "=== SYLPHY R5 Windows Acceptance ===" -ForegroundColor Cyan
Write-Host "Repository: $Root"

python --version
if ($LASTEXITCODE -ne 0) { throw "Python is unavailable." }

Push-Location $Avatar
try {
    if (-not (Test-Path "node_modules\electron\dist\electron.exe")) {
        npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install failed." }
    }
    npm run desktop:build
    if ($LASTEXITCODE -ne 0) { throw "Avatar production build failed." }
    npm test
    if ($LASTEXITCODE -ne 0) { throw "Avatar tests failed." }
} finally { Pop-Location }

$env:PYTHONPATH = Join-Path $Root "src"
python -m sylphy_entity.embodiment.avatar_service status
if ($LASTEXITCODE -ne 0) { throw "Avatar status command failed." }
python -m sylphy_entity.embodiment.avatar_service start
if ($LASTEXITCODE -ne 0) { throw "Avatar start failed." }
Start-Sleep -Seconds 3
python -m sylphy_entity.embodiment.avatar_service status
if ($LASTEXITCODE -ne 0) { throw "Avatar status after start failed." }

Write-Host ""
Write-Host "MANUAL WINDOWS CHECKS REQUIRED:" -ForegroundColor Yellow
Write-Host "1. Confirm transparent frameless window and always-on-top."
Write-Host "2. Drag by top bar; resize from window edges."
Write-Host "3. Ctrl+Shift+S hides/shows; tray menu can restore; Ctrl+Shift+Q quits."
Write-Host "4. Stop/restart Avatar and verify saved window bounds restore."
Write-Host "5. Close/kill Avatar; verify Entity /health remains available."
Write-Host "6. Restart Avatar; verify Entity badge reconnects."
Write-Host "7. Confirm no camera/microphone permission or capture is requested."
Write-Host "When finished, run: python -m sylphy_entity.embodiment.avatar_service stop"
Write-Host "Scripted checks completed; manual visual/process acceptance is not auto-PASS." -ForegroundColor Green
