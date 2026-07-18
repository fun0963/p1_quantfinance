# trading.ps1 - one-click start/stop/status for the paper-trading schedulers.
#
# Deliberately NOT a Windows Task Scheduler service: this machine is switched
# off irregularly, so trading runs only while a human (or Claude) starts it.
# Nothing here survives reboot - rerun after every boot when you want trading on.
#
#   trading.cmd                -> start both schedulers (double-click friendly)
#   trading.cmd stop           -> kill all quant schedulers (broker positions untouched)
#   trading.cmd status         -> show what is running
#   trading.cmd start spy      -> start only jobs whose name contains 'spy'
#
# Duplicate-guard: start skips a job when a scheduler for the same spec is
# already running (two schedulers on one symbol would fight over the position).

param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "start",
    [string]$Job = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $RepoRoot
$env:PYTHONPATH = "src"
$PY = Join-Path $RepoRoot ".venv\Scripts\python.exe"
$LogDir = Join-Path $RepoRoot "logs"
if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir | Out-Null }

# --- the jobs (edit here when specs or sizing change) ------------------------
$Jobs = @(
    @{ Name = "spy_momentum"
       Args = "-u -m quant.cli schedule --spec spy_momentum --broker alpaca --fraction 0.45 --run-now --execute" },
    @{ Name = "qqq_scalp_1min"
       Args = "-u -m quant.cli schedule --spec qqq_scalp_1min --broker alpaca --every 5min --fraction 0.5 --execute" }
)

function Get-SchedulerProcs {
    Get-CimInstance Win32_Process |
        Where-Object { $_.Name -in @("python.exe", "quant.exe") -and
                       $_.CommandLine -match "quant" -and $_.CommandLine -match "schedule" }
}

switch ($Action) {
    "status" {
        $procs = @(Get-SchedulerProcs)
        if ($procs.Count -eq 0) {
            Write-Host "no quant schedulers running"
        } else {
            foreach ($p in $procs) {
                Write-Host ("PID {0}  started {1}" -f $p.ProcessId, $p.CreationDate)
                Write-Host ("    {0}" -f $p.CommandLine)
            }
        }
        Write-Host ""
        Write-Host "more detail: quant health / quant journal --live / quant oms"
    }
    "stop" {
        $procs = @(Get-SchedulerProcs)
        if ($procs.Count -eq 0) { Write-Host "nothing to stop"; break }
        foreach ($p in $procs) {
            Write-Host ("stopping PID {0}" -f $p.ProcessId)
            try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {}
        }
        Write-Host "done. broker positions and protective brackets are NOT touched."
    }
    "start" {
        $started = 0; $skipped = 0; $failed = 0
        foreach ($j in $Jobs) {
            if ($Job -and ($j.Name -notmatch [regex]::Escape($Job))) { continue }
            $existing = @(Get-SchedulerProcs | Where-Object { $_.CommandLine -match [regex]::Escape($j.Name) })
            if ($existing.Count -gt 0) {
                Write-Host ("SKIP  {0}: already running (PID {1})" -f $j.Name, $existing[0].ProcessId)
                $skipped++
                continue
            }
            $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
            $out = Join-Path $LogDir ("schedule_{0}_{1}.log" -f $j.Name, $stamp)
            # cmd /c merges stdout+stderr into ONE log (loguru operational lines go
            # to stderr; a split .err.log hides the interesting half).
            $inner = "`"$PY`" {0} >> `"$out`" 2>&1" -f $j.Args
            $p = Start-Process -FilePath "cmd.exe" -ArgumentList "/d /s /c `"$inner`"" `
                    -WorkingDirectory $RepoRoot -WindowStyle Hidden -PassThru
            Start-Sleep -Seconds 3
            if ($p.HasExited) {
                Write-Host ("FAIL  {0}: exited immediately (code {1}) - see {2}" -f $j.Name, $p.ExitCode, $out)
                $failed++
            } else {
                Write-Host ("OK    {0}: PID {1}  log {2}" -f $j.Name, $p.Id, $out)
                $started++
            }
        }
        Write-Host ""
        Write-Host ("started {0}, skipped {1}, failed {2}" -f $started, $skipped, $failed)
        Write-Host "NOTE: does not survive reboot - rerun this after every boot."
        Write-Host "check: quant health    stop: trading.cmd stop"
        if ($failed -gt 0) { exit 1 }
    }
}
