#!/bin/sh
# Run every pre-registered check on the recent period (2025-12-08..12, 2026-05-15, 05-18, 06-12).
# Fees are the bot default (0.25% per side). Outputs land next to the data and in the repository.
# Requires Python on PATH and the extracted data in /d/us-stock-data/recent.
cd /d/us-stock-bot || exit 1
OUT=/d/us-stock-data/recent-results
mkdir -p "$OUT"
echo "$(date +%T) strategies"
python dashboard.py --historical "D:\us-stock-data\recent\sessions" --utc-offset=-5 --no-open \
  --output historical-recent-dashboard.html > "$OUT/dashboard.log" 2>&1 || exit 1
echo "$(date +%T) fees"
PYTHONIOENCODING=utf-8 python fees.py historical-recent-dashboard.html > "$OUT/fees.txt" 2>&1
echo "$(date +%T) diagnose"
PYTHONIOENCODING=utf-8 python diagnose.py historical-recent-dashboard.html "D:/us-stock-data/recent/sessions" > "$OUT/diagnose.json" 2>&1
echo "$(date +%T) news"
python news_study.py "D:/us-stock-data/recent/sessions" > "$OUT/news.json" 2>&1
echo "$(date +%T) done"
