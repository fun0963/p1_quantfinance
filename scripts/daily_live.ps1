# daily_live.ps1 — one daily live run, for Windows Task Scheduler to call.
# Edit the symbol/strategy/params below to your live config, then register this
# script with Task Scheduler (see docs/SCHEDULING.md). Logs go to logs/.
$ErrorActionPreference = "Stop"
Set-Location "D:\AI_work_claude\p1_quantfinance"
$env:PYTHONPATH = "src"
$PY = ".\.venv\Scripts\python.exe"

# --- your live config -------------------------------------------------------
# Remove --execute to keep it a dry-run (decisions journaled, no orders).
& $PY -m quant.cli live SPY `
    --strategy momentum --params "lookback=100" `
    --broker alpaca --mode target `
    --stop-loss 0.05 --take-profit 0.15 `
    --execute
