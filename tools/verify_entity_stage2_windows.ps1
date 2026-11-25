$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $PSCommandPath
$projectRoot = $scriptRoot
if ((Split-Path -Leaf $scriptRoot) -eq "tools") {
    $projectRoot = Split-Path -Parent $scriptRoot
}
Push-Location $projectRoot

function Resolve-EntityCommand([string]$Name) {
    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if ($null -ne $command -and $command.CommandType -eq "Application") {
        return $command.Source
    }
    $scriptsDir = & py -3.13 -c "import sysconfig; print(sysconfig.get_path('scripts'))" 2>$null
    if ($LASTEXITCODE -eq 0 -and $scriptsDir) {
        $scriptsDir = ($scriptsDir | Select-Object -Last 1).ToString().Trim()
        $candidate = Join-Path $scriptsDir "$Name.exe"
        if (Test-Path $candidate) { return $candidate }
    }
    throw "Cannot find $Name. Install the editable project using Python 3.13."
}

function Remove-TestSandbox([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) { return $true }
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        try {
            Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction SilentlyContinue | ForEach-Object {
                try { $_.Attributes = [System.IO.FileAttributes]::Normal } catch {}
            }
            Remove-Item -LiteralPath $Path -Recurse -Force -ErrorAction Stop
            return $true
        } catch {
            Start-Sleep -Milliseconds (250 * $attempt)
        }
    }
    return $false
}

function Wait-World([string]$Url, [scriptblock]$Predicate, [int]$Seconds = 10) {
    $deadline = (Get-Date).AddSeconds($Seconds)
    do {
        try {
            $snapshot = Invoke-RestMethod -Uri "$Url/world" -TimeoutSec 2
            if (& $Predicate $snapshot) { return $snapshot }
        } catch {
            Start-Sleep -Milliseconds 150
        }
        Start-Sleep -Milliseconds 100
    } while ((Get-Date) -lt $deadline)
    throw "World Model did not reach the expected state before timeout."
}

$daemon = $null
$port = 18766
$baseUrl = "http://127.0.0.1:$port"
$sandbox = Join-Path $env:TEMP "sylphy-entity-stage2-$PID"
$daemonExe = $null
$clientExe = $null

try {
    Write-Host "Repository: $projectRoot"
    Write-Host "[1/7] Checking Python and Stage 2 package..."
    py -3.13 --version
    if ($LASTEXITCODE -ne 0) { throw "Python 3.13 is required." }
    $daemonExe = Resolve-EntityCommand "sylphy-entity"
    $clientExe = Resolve-EntityCommand "sylphy-entityctl"
    $null = New-Item -ItemType Directory -Force -Path $sandbox
    Write-Host "PASS: prerequisites"

    Write-Host "[2/7] Starting Daemon with an explicitly authorized sandbox..."
    $daemonArgs = @("--port", "$port", "--sandbox-root", "`"$sandbox`"", "--reconcile-interval", "0.25")
    $daemon = Start-Process -FilePath $daemonExe -ArgumentList $daemonArgs -WindowStyle Hidden -PassThru
    $deadline = (Get-Date).AddSeconds(15)
    do {
        try { $health = Invoke-RestMethod -Uri "$baseUrl/health" -TimeoutSec 2; break }
        catch { Start-Sleep -Milliseconds 250 }
    } while ((Get-Date) -lt $deadline)
    if ($null -eq $health -or $health.status -ne "ok") { throw "Daemon did not become healthy." }
    $status = Invoke-RestMethod -Uri "$baseUrl/status" -TimeoutSec 3
    if ($status.world.adapter.backend -ne "watchdog+reconciliation") {
        throw "Expected watchdog backend, found '$($status.world.adapter.backend)'. Install watchdog with: py -3.13 -m pip install -e `".[dev,world]`""
    }
    Write-Host "PASS: daemon is using watchdog + periodic reconciliation"

    Write-Host "[3/7] Checking initial sandbox discovery..."
    $initial = Join-Path $sandbox "initial.txt"
    Set-Content -Path $initial -Value "seed" -NoNewline
    $null = Wait-World $baseUrl { param($w) @($w.facts | Where-Object { $_.subject -eq "file:initial.txt" -and $_.state -eq "known" }).Count -eq 1 }
    Write-Host "PASS: discovered existing file"

    Write-Host "[4/7] Checking create and modify observations..."
    $created = Join-Path $sandbox "created.txt"
    Set-Content -Path $created -Value "one" -NoNewline
    $null = Wait-World $baseUrl { param($w) @($w.facts | Where-Object { $_.subject -eq "file:created.txt" -and $_.state -eq "known" }).Count -eq 1 }
    Set-Content -Path $created -Value "modified content" -NoNewline
    $null = Wait-World $baseUrl { param($w) $f = $w.facts | Where-Object { $_.subject -eq "file:created.txt" }; $null -ne $f -and $f.value.size_bytes -gt 3 }
    Write-Host "PASS: create and modify"

    Write-Host "[5/7] Checking rename semantics..."
    $renamed = Join-Path $sandbox "renamed.txt"
    Move-Item -Path $created -Destination $renamed
    $null = Wait-World $baseUrl { param($w) $new = $w.facts | Where-Object { $_.subject -eq "file:renamed.txt" }; $old = $w.facts | Where-Object { $_.subject -eq "file:created.txt" }; $null -ne $new -and $new.state -eq "known" -and $null -ne $old -and $old.state -eq "unknown" }
    Write-Host "PASS: rename updates old and new subjects"

    Write-Host "[6/7] Checking delete semantics..."
    Remove-Item -Path $renamed
    $null = Wait-World $baseUrl { param($w) $f = $w.facts | Where-Object { $_.subject -eq "file:renamed.txt" }; $null -ne $f -and $f.state -eq "unknown" }
    Write-Host "PASS: deleted file becomes UNKNOWN"

    Write-Host "[7/7] Stopping Daemon and cleaning test sandbox..."
    & $clientExe --url $baseUrl stop | Out-Host
    if (-not $daemon.WaitForExit(10000)) { throw "Daemon did not stop cleanly." }
    if (Remove-TestSandbox $sandbox) {
        Write-Host "PASS: temporary sandbox cleanup"
    } else {
        Write-Warning "Daemon verification passed, but Windows refused to remove the temporary sandbox: $sandbox. Remove it manually after confirming no Sylphy Entity process is running."
    }
    Write-Host "PASS: Stage 2 Windows filesystem smoke test completed"
}
finally {
    if ($null -ne $daemon -and -not $daemon.HasExited) {
        try { & $clientExe --url $baseUrl stop | Out-Host } catch { Stop-Process -Id $daemon.Id -Force -ErrorAction SilentlyContinue }
    }
    if (Test-Path -LiteralPath $sandbox) {
        if (-not (Remove-TestSandbox $sandbox)) {
            Write-Warning "Temporary sandbox remains at $sandbox; remove it manually after the Daemon has exited."
        }
    }
    Pop-Location
}
