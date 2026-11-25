$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
$Avatar = Join-Path $Root 'avatar'
$PreviousPythonPath = $env:PYTHONPATH
$env:PYTHONPATH = Join-Path $Root 'src'

try {
    Write-Host "`n=== R6 VOICE LIP-SYNC WINDOWS ACCEPTANCE ===" -ForegroundColor Cyan
    Write-Host "Repository: $Root"

    $PythonVersion = python --version 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'Python was not found on PATH.' }
    Write-Host "Python: $PythonVersion"

    Write-Host "`n=== ENTITY VOICE COMPILE ===" -ForegroundColor Cyan
    python -m compileall -q (Join-Path $Root 'src\sylphy_entity\voice') (Join-Path $Root 'tests\entity_voice')
    if ($LASTEXITCODE -ne 0) { throw 'Python compile check failed.' }

    Write-Host "`n=== ENTITY VOICE TESTS ===" -ForegroundColor Cyan
    python -m pytest (Join-Path $Root 'tests\entity_voice') -q
    if ($LASTEXITCODE -ne 0) { throw 'Entity Voice tests failed.' }

    Write-Host "`n=== ELECTRON IPC SYNTAX ===" -ForegroundColor Cyan
    node --check (Join-Path $Avatar 'desktop\main.cjs')
    if ($LASTEXITCODE -ne 0) { throw 'Electron main process syntax check failed.' }
    node --check (Join-Path $Avatar 'desktop\preload.cjs')
    if ($LASTEXITCODE -ne 0) { throw 'Electron preload syntax check failed.' }

    Push-Location $Avatar
    try {
        Write-Host "`n=== AVATAR TYPESCRIPT / VITE BUILD ===" -ForegroundColor Cyan
        npm run build
        if ($LASTEXITCODE -ne 0) { throw 'Avatar build failed.' }

        Write-Host "`n=== AVATAR VITEST ===" -ForegroundColor Cyan
        npm test
        if ($LASTEXITCODE -ne 0) { throw 'Avatar Vitest suite failed.' }
    }
    finally {
        Pop-Location
    }

    Write-Host "`n=== ENTITY AND VOICE API ===" -ForegroundColor Cyan
    $Health = Invoke-RestMethod 'http://127.0.0.1:8765/health'
    if ($Health.status -ne 'ok' -or $Health.lifecycle -ne 'running') {
        throw 'Entity /health is not healthy/running.'
    }
    $Voice = Invoke-RestMethod 'http://127.0.0.1:8765/voice/status'
    if ($Voice.enabled -ne $true) { throw 'Voice Output is not enabled on the running Entity.' }
    $RequiredFields = @('state','is_speaking','active_session_id','entity_instance_id','session_generation','sequence','position_seconds','duration_seconds','amplitude')
    foreach ($Field in $RequiredFields) {
        if ($Voice.PSObject.Properties.Name -notcontains $Field) {
            throw "Voice status is missing field '$Field'."
        }
    }
    $Voice | ConvertTo-Json -Depth 5

    Write-Host "`n=== MANUAL WINDOWS ACCEPTANCE REQUIRED ===" -ForegroundColor Yellow
    Write-Host '1. Start Avatar and confirm the bundled Sylphy.vrm is loaded.'
    Write-Host '2. Keep the mouth closed while Voice is synthesizing/ready (before playback_started).'
    Write-Host '3. Speak one short test utterance and observe the mouth moving while sound is actually playing.'
    Write-Host '4. During playback, confirm /voice/status position_seconds advances and amplitude stays in [0,1].'
    Write-Host '5. POST /voice/stop; confirm state=interrupted, is_speaking=false, amplitude=0, and mouth closes.'
    Write-Host '6. Start another utterance; confirm the new session controls lip-sync.'
    Write-Host '7. Close Avatar; confirm Entity /health remains ok.'
    Write-Host 'Use docs/entity_rework/STAGE_7_PHASE_6_VOICE_AVATAR_SYNC.md for sample /voice/speak and /voice/stop requests.'
    Write-Host 'No microphone or webcam is used.'
    Write-Host "`nAutomated R6 checks completed. Do not mark Windows acceptance PASS until the manual speaker/VRM checks above are observed." -ForegroundColor Green
}
finally {
    $env:PYTHONPATH = $PreviousPythonPath
}
