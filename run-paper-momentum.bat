@echo off
rem Monthly momentum paper portfolio: download latest prices, update paper\momentum.md, commit and push.
rem Double-click to run by hand. The scheduled task calls it with "auto" (no pause, output to a log file).
rem No real orders are placed.
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if "%1"=="auto" goto auto

%PY% paper_momentum.py
if errorlevel 1 (
  echo Failed. Copy the message above and show it to Claude.
  pause
  exit /b 1
)
git add paper\momentum.md paper\momentum.json
git commit -m "Paper momentum update"
git push origin main
start "" "paper\momentum.md"
pause
exit /b 0

:auto
set LOG=paper\last-run.log
echo ==== %DATE% %TIME% > %LOG%
%PY% paper_momentum.py >> %LOG% 2>&1
if errorlevel 1 (
  echo FAILED >> %LOG%
  exit /b 1
)
git add paper\momentum.md paper\momentum.json >> %LOG% 2>&1
git commit -m "Paper momentum update (scheduled)" >> %LOG% 2>&1
set GIT_TERMINAL_PROMPT=0
git push origin main >> %LOG% 2>&1
echo DONE >> %LOG%
exit /b 0
