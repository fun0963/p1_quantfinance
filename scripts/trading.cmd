@echo off
rem One-click trading launcher. Double-click = start both paper schedulers.
rem Also: trading.cmd stop | trading.cmd status | trading.cmd start spy
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0trading.ps1" %*
echo.
pause
