$ErrorActionPreference = "Stop"

# Accept this script either in the repository root or in the tools\ folder.
$scriptRoot = Split-Path -Parent $PSCommandPath
$projectRoot = $scriptRoot
if ((Split-Path -Leaf $scriptRoot) -eq "tools") {
    $projectRoot = Split-Path -Parent $scriptRoot
}
Push-Location $projectRoot

function Resolve-EntityCommand([string]$Name) {
    # First try PATH.
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $command -and $command.CommandType -eq "Application") {
        return $command.Source
    }

    # pip may have installed the console launcher into Python's Scripts folder,
    # even when that folder is not included in this PowerShell session's PATH.
    $scriptsDir = & py -3.13 -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>$null
    if ($LASTEXITCODE -eq 0 -and $scriptsDir) {
        $scriptsDir = ($scriptsDir | Select-Object -Last 1).ToString().Trim()
        $candidate = Join-Path $scriptsDir "$Name.exe"
        if (Test-Path $candidate) {
            return $candidate
        }
    }

    throw @"
Cannot find '$Name'. The Entity package is either not installed in Python 3.13,
or Python's Scripts folder is unavailable.

From the repository root, run:
  py -3.13 -m pip install -e ".[dev]"

Then verify:
  Get-Command sylphy-entity
  Get-Command sylphy-entityctl

You can rerun this script after installation. Current repository: $projectRoot
"@
}

$daemon = $null
$secondDaemon = $null
$port = 18765
$baseUrl = "http://127.0.0.1:$port"

function Wait-EntityHealth([string]$Url, [int]$Seconds = 15) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            $health = Invoke-RestMethod -Uri "$Url/health" -TimeoutSec 2
            if ($health.status -eq "ok") { return $health }
        } catch {
            Start-Sleep -Milliseconds 250
        }
    }
    throw "Entity Daemon did not become healthy at $Url"
}

function Stop-EntityProcess($Process, [string]$Url) {
    if ($null -eq $Process) { return }
    try {
        & $clientExe --url $Url stop | Out-Host
    } catch {
        if (-not $Process.HasExited) {
            Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        }
    }
    try { $Process.WaitForExit(10000) | Out-Null } catch { }
}

try {
    Write-Host "Repository: $projectRoot"
    Write-Host "[1/8] Checking Python 3.13..."
    py -3.13 --version
    if ($LASTEXITCODE -ne 0) { throw "Python 3.13 was not found through the Windows py launcher." }

    $daemonExe = Resolve-EntityCommand "sylphy-entity"
    $clientExe = Resolve-EntityCommand "sylphy-entityctl"
    Write-Host "Daemon launcher: $daemonExe"
    Write-Host "Client launcher: $clientExe"

    Write-Host "[2/8] Starting Daemon as a detached process..."
    $daemon = Start-Process -FilePath $daemonExe -ArgumentList @("--port", "$port") -WindowStyle Hidden -PassThru
    $health = Wait-EntityHealth $baseUrl
    Write-Host "PASS: $($health.lifecycle)"

    Write-Host "[3/8] Querying from a separate CLI process..."
    $status = & $clientExe --url $baseUrl status | ConvertFrom-Json
    if ($status.lifecycle -ne "running") { throw "Unexpected lifecycle: $($status.lifecycle)" }
    if ($status.api.host -ne "127.0.0.1") { throw "API is not bound to loopback" }
    Write-Host "PASS: CLI client can inspect the detached Daemon"

    Write-Host "[4/8] Publishing an event, then checking it was processed..."
    & $clientExe --url $baseUrl publish --type "windows.smoke_test" --source "operator" --payload-json '{"check":"event-ingress"}' | Out-Host
    $deadline = (Get-Date).AddSeconds(5)
    do {
        $status = & $clientExe --url $baseUrl status | ConvertFrom-Json
        if ($status.events.processed -ge 1) { break }
        Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $deadline)
    if ($status.events.processed -lt 1) { throw "Published event was not processed" }
    Write-Host "PASS: event ingress"

    Write-Host "[5/8] Confirming a second Daemon is refused..."
    $secondDaemon = Start-Process -FilePath $daemonExe -ArgumentList @("--port", "$($port + 1)") -WindowStyle Hidden -PassThru -Wait
    if ($secondDaemon.ExitCode -ne 2) { throw "Second Daemon exit code was $($secondDaemon.ExitCode), expected 2" }
    $null = Wait-EntityHealth $baseUrl
    Write-Host "PASS: single-instance lock; original Daemon remains healthy"

    Write-Host "[6/8] Requesting graceful shutdown from the client..."
    & $clientExe --url $baseUrl stop | Out-Host
    if (-not $daemon.WaitForExit(10000)) { throw "Daemon did not stop within 10 seconds" }
    Write-Host "PASS: client shutdown; Daemon process exited"

    Write-Host "[7/8] Starting again and checking the clean-shutdown marker..."
    $daemon = Start-Process -FilePath $daemonExe -ArgumentList @("--port", "$port") -WindowStyle Hidden -PassThru
    $null = Wait-EntityHealth $baseUrl
    $status = & $clientExe --url $baseUrl status | ConvertFrom-Json
    if ($status.persistence.previous_shutdown_clean -ne $true) {
        throw "Previous shutdown was not recorded as clean"
    }
    if ($status.persistence.boot_count -lt 2) { throw "Boot count did not increment" }
    Write-Host "PASS: SQLite WAL runtime continuity marker"

    Write-Host "[8/8] Final graceful shutdown..."
    Stop-EntityProcess $daemon $baseUrl
    Write-Host "PASS: Stage 1 Windows runtime smoke test completed"
}
finally {
    if ($null -ne $daemon -and -not $daemon.HasExited) {
        Stop-EntityProcess $daemon $baseUrl
    }
    Pop-Location
}
