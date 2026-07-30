# run_watchdog.ps1 - auto-resume watchdog for detached variant-pool runs.
# Policy (user-approved Jul-30): act ONLY on confirmed process death;
# restart ONLY via the pre-registered *_resume scheduled task (--resume,
# never --clean); hard cap on auto-restarts; every decision audited.
# A live-but-slow run (long model call) is never killed or restarted.
param(
    [Parameter(Mandatory = $true)][string]$RunTag,
    [string]$RunsDir = "D:\PycharmProj\HarnessX\recipe\gaia_evolver\runs",
    [string]$ResumeTaskName = "",
    [int]$MaxRestarts = 2,
    [int]$PollSeconds = 120,
    [int]$InitialGraceSeconds = 60,
    [switch]$DryRun
)
if (-not $ResumeTaskName) { $ResumeTaskName = "HarnessX_${RunTag}_resume" }
$audit = Join-Path $RunsDir "$RunTag.watchdog.log"
$stateFile = Join-Path $RunsDir "$RunTag.watchdog.state"

function Write-Audit($msg) {
    "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $msg" | Add-Content -Path $audit
}
function Get-RestartCount {
    if (Test-Path $stateFile) { return [int](Get-Content $stateFile -TotalCount 1) }
    return 0
}
function Set-RestartCount($n) { Set-Content -Path $stateFile -Value $n }
function Test-RunAlive {
    $p = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like "*$RunTag*" -and $_.CommandLine -like "*run_variant_pool*" }
    return ($null -ne $p)
}
function Get-LastExitMarker {
    # Resume log first (newer attempts win), then the original launch log.
    foreach ($f in @("$RunTag.resume.console.log", "$RunTag.console.log")) {
        $path = Join-Path $RunsDir $f
        if (Test-Path $path) {
            $m = Select-String -Path $path -Pattern "LAUNCHER_EXIT code=(\d+)" -ErrorAction SilentlyContinue |
                Select-Object -Last 1
            if ($m) { return @{ found = $true; code = [int]$m.Matches[0].Groups[1].Value } }
        }
    }
    return @{ found = $false; code = -1 }
}

Write-Audit "WATCHDOG START tag=$RunTag maxRestarts=$MaxRestarts poll=${PollSeconds}s dryRun=$DryRun"
if ($InitialGraceSeconds -gt 0) { Start-Sleep -Seconds $InitialGraceSeconds }

while ($true) {
    if (Test-RunAlive) { Start-Sleep -Seconds $PollSeconds; continue }
    Start-Sleep -Seconds 15   # let the launcher shell write its EXIT marker
    $ex = Get-LastExitMarker
    if ($ex.found -and $ex.code -eq 0) {
        Write-Audit "run completed with exit 0 - standing down"
        break
    }
    $n = Get-RestartCount
    if ($n -ge $MaxRestarts) {
        Write-Audit "DEAD (exitMarker found=$($ex.found) code=$($ex.code)) but max restarts ($MaxRestarts) reached - standing down, manual intervention required"
        break
    }
    Set-RestartCount ($n + 1)
    Write-Audit "DEAD detected (exitMarker found=$($ex.found) code=$($ex.code)); AUTO-RESUME attempt $($n + 1)/$MaxRestarts via task $ResumeTaskName"
    if ($DryRun) {
        Write-Audit "DRYRUN: would Start-ScheduledTask $ResumeTaskName"
        break
    }
    Start-ScheduledTask -TaskName $ResumeTaskName
    Start-Sleep -Seconds 180  # boot grace for the resumed run
}
Write-Audit "WATCHDOG END"
