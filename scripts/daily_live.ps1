# daily_live.ps1 — one daily live run, for Windows Task Scheduler to call.
# Edit the symbol/strategy/params below to your live config, then register this
# script with Task Scheduler (see docs/SCHEDULING.md). Logs go to logs/.
#
# SAFETY: this template runs a DRY-RUN (decisions journaled, no orders). Only add
# `--execute` once you've watched `quant journal --live` for a few days and trust it.
$ErrorActionPreference = "Stop"
Set-Location "D:\AI_work_claude\p1_quantfinance"
$env:PYTHONPATH = "src"
$PY = ".\.venv\Scripts\python.exe"

# --- your live config -------------------------------------------------------
# Add `--execute` to the end of this command ONLY when you want it to place real
# (paper) orders. Keep --stop-loss/--take-profit so entries are auto-protected.
& $PY -m quant.cli live SPY `
    --strategy momentum --params "lookback=100" `
    --broker alpaca --mode target `
    --stop-loss 0.05 --take-profit 0.15 --max-daily-loss 3000

# PowerShell's $ErrorActionPreference does NOT catch a native exe's non-zero exit,
# so check it explicitly — otherwise a failed run is reported to Task Scheduler as
# success and you'd never know the system stopped trading.
if ($LASTEXITCODE -ne 0) {
    Write-Error "quant live FAILED (exit $LASTEXITCODE) — check logs/. Task Scheduler will mark this run failed."
    exit $LASTEXITCODE
}
