@echo off
rem Monthly momentum paper portfolio: download latest prices, update paper\momentum.md, commit and push.
rem Double-click once a month (first week). No real orders are placed.
cd /d "%~dp0"
python paper_momentum.py
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
