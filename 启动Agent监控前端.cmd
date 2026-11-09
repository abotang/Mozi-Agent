@echo off
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
    python "%~dp0monitor_dashboard.py"
) else (
    py -3 "%~dp0monitor_dashboard.py"
)
if errorlevel 1 pause
