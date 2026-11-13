@echo off
cd /d "%~dp0"
where py >nul 2>&1
if errorlevel 1 (
    python -B dashboard.py
) else (
    py -3 -B dashboard.py
)
if errorlevel 1 pause
