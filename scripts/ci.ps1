# ci.ps1 — run the same checks CI runs, locally. Mirrors .github/workflows/ci.yml.
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\ci.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)   # repo root
$PY = ".\.venv\Scripts\python.exe"

Write-Host "`n=== ruff (lint + import order) ===" -ForegroundColor Cyan
& $PY -m ruff check src tests config
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== mypy (type check) ===" -ForegroundColor Cyan
& $PY -m mypy
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== pytest ===" -ForegroundColor Cyan
& $PY -m pytest -q
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`nAll green." -ForegroundColor Green
