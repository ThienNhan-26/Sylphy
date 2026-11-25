[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$Model,
    [int]$Port = 18765,
    [int]$TimeoutSeconds = 240
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Assert-Command([string]$Name) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found."
    }
}

Assert-Command 'py'
Assert-Command 'ollama'

Write-Host '[1/5] Checking Python 3.13 and optional Cognition dependencies...' -ForegroundColor Cyan
py -3.13 -c "import sys, httpx; assert sys.version_info >= (3,13), sys.version; print('Python', sys.version.split()[0], '| httpx', httpx.__version__)"
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.13 or httpx is unavailable. Install with: py -3.13 -m pip install -e ".[cognition,dev,world]"'
}

Write-Host '[2/5] Checking the exact Ollama model tag...' -ForegroundColor Cyan
ollama show $Model *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Ollama model '$Model' is unavailable. Check 'ollama list' and pass its exact tag with -Model."
}

$sandbox = Join-Path $repoRoot '.stage7_cognition_acceptance_sandbox'
$logDir = Join-Path $repoRoot 'logs'
New-Item -ItemType Directory -Force -Path $sandbox, $logDir | Out-Null
Get-ChildItem -LiteralPath $sandbox -Force -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force
$stdout = Join-Path $logDir 'stage7-cognition-daemon.stdout.log'
$stderr = Join-Path $logDir 'stage7-cognition-daemon.stderr.log'
Remove-Item $stdout, $stderr -Force -ErrorAction SilentlyContinue

$runtimeDir = Join-Path $env:LOCALAPPDATA 'SYLPHY\EntityRuntime'
$tokenPath = Join-Path $runtimeDir 'control.token'
$daemon = $null
$passed = $false

try {
    Write-Host '[3/5] Starting an isolated Stage 7 Cognition daemon...' -ForegroundColor Cyan
    $arguments = @(
        '-3.13', '-m', 'sylphy_entity.daemon',
        '--port', "$Port",
        '--sandbox-root', ('"' + $sandbox + '"'),
        '--autonomy',
        '--cognition-model', ('"' + $Model + '"')
    )
    $daemon = Start-Process -FilePath 'py' -ArgumentList ($arguments -join ' ') -WorkingDirectory $repoRoot `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru -WindowStyle Hidden

    $health = $false
    $deadline = (Get-Date).AddSeconds(30)
    while ((Get-Date) -lt $deadline) {
        if ($daemon.HasExited) { throw "Entity daemon exited during startup. See $stderr" }
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/health" -TimeoutSec 2
            if ($response.status -eq 'ok') { $health = $true; break }
        } catch { Start-Sleep -Milliseconds 500 }
    }
    if (-not $health) { throw 'Entity daemon did not become healthy within 30 seconds.' }

    Write-Host '[4/5] Creating a metadata-only test file and waiting for Brain review...' -ForegroundColor Cyan
    Set-Content -LiteralPath (Join-Path $sandbox 'stage7-cognition-probe.txt') `
        -Value 'This content must not be sent to the cognition model.' -Encoding UTF8

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    $acceptedReview = @()
    $lastStatus = $null
    while ((Get-Date) -lt $deadline) {
        if ($daemon.HasExited) { throw "Entity daemon exited during Cognition. See $stderr" }
        try {
            $lastStatus = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/status" -TimeoutSec 3
            $cognition = $lastStatus.cognition
            if ($cognition.reviewed -gt 0 -and $cognition.recent_reviews.Count -gt 0) {
                $acceptedReview = @($cognition.recent_reviews | Where-Object {
                    $_.status -eq 'completed' -and $_.accepted_as_evidence -eq $true
                } | Select-Object -Last 1)
                if ($acceptedReview.Count -gt 0) { break }
            }
        } catch { }
        Start-Sleep -Seconds 2
    }

    if ($null -eq $lastStatus) { throw 'Could not read Entity /status.' }
    if ($lastStatus.cognition.enabled -ne $true) { throw 'Cognition provider is not enabled in runtime status.' }
    if ($lastStatus.cognition.requested -lt 1) { throw 'No Brain-originated Cognition request was journaled.' }
    if ($lastStatus.cognition.completed -lt 1) { throw 'No Cognition provider result was completed.' }
    if ($lastStatus.cognition.reviewed -lt 1) { throw 'Brain did not review a Cognition result.' }
    if ($acceptedReview.Count -lt 1) {
        $reviews = $lastStatus.cognition.recent_reviews | ConvertTo-Json -Depth 8
        throw "No completed result was accepted as evidence by Brain before timeout. Recent reviews: $reviews"
    }

    $review = $acceptedReview[0]
    Write-Host "PASS: Stage 7 live Cognition accepted by Brain" -ForegroundColor Green
    Write-Host "  Model:       $($review.model)"
    Write-Host "  Request ID:  $($review.request_id)"
    Write-Host "  Elapsed:     $($review.elapsed_ms) ms"
    Write-Host "  Requested:   $($lastStatus.cognition.requested)"
    Write-Host "  Completed:   $($lastStatus.cognition.completed)"
    Write-Host "  Reviewed:    $($lastStatus.cognition.reviewed)"
    Write-Host '  Evidence:    completed + accepted_as_evidence=true'
    $passed = $true
}
finally {
    if ($null -ne $daemon -and -not $daemon.HasExited) {
        try {
            if (Test-Path $tokenPath) {
                $token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
                Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:$Port/shutdown" `
                    -Headers @{ 'X-Sylphy-Control-Token' = $token } -TimeoutSec 5 | Out-Null
                if (-not $daemon.WaitForExit(15000)) { $daemon.Kill(); $daemon.WaitForExit() }
            } else {
                $daemon.Kill(); $daemon.WaitForExit()
            }
        } catch {
            if (-not $daemon.HasExited) { $daemon.Kill(); $daemon.WaitForExit() }
        }
    }
}

if (-not $passed) { throw 'Stage 7 live Cognition acceptance did not pass.' }
Write-Host 'PASS: Stage 7 Slice 1 Windows live acceptance completed' -ForegroundColor Green
Write-Host "Daemon logs: $stdout ; $stderr"
