#!/bin/sh
# Extract the wider universe (symbols.txt, fixed from the first sample day) for all 7 verified days in parallel.
# Output: /d/us-stock-data/wide/sessions (trading symbols) and /d/us-stock-data/wide/index (SPY, QQQ).
# Requires the downloads from download-itch.sh (*.ok files) and Python on PATH.
WIDE=/d/us-stock-data/wide
cd /d/us-stock-data/itch || exit 1
SYMS="$(tr '\r\n' '  ' < "$WIDE/symbols.txt") SPY QQQ"
for pair in 01302019.NASDAQ_ITCH50.gz:2019-01-30 03272019.NASDAQ_ITCH50.gz:2019-03-27 07302019.NASDAQ_ITCH50.gz:2019-07-30 08302019.NASDAQ_ITCH50.gz:2019-08-30 S101819-v50.txt.gz:2019-10-18 10302019.NASDAQ_ITCH50.gz:2019-10-30 12302019.NASDAQ_ITCH50.gz:2019-12-30; do
  f=${pair%%:*}; d=${pair##*:}
  [ -f "$f.ok" ] || { echo "$(date +%T) skip $f (not verified)"; continue; }
  [ -d "$WIDE/sessions/$d" ] && continue
  (
    echo "$(date +%T) extract $f -> $d"
    if python -I -B /d/us-stock-bot/itch_extract.py "$f" "$WIDE/sessions" "$d" $SYMS --allow-missing > "$WIDE/extract-$d.txt" 2>&1; then
      mkdir -p "$WIDE/index/$d"
      mv "$WIDE/sessions/$d/SPY" "$WIDE/sessions/$d/QQQ" "$WIDE/index/$d/"
      echo "$(date +%T) done $d ($(grep -c missing "$WIDE/extract-$d.txt") missing)"
    else
      echo "$(date +%T) EXTRACT FAILED $d"; tail -3 "$WIDE/extract-$d.txt"
    fi
  ) &
done
wait
echo "$(date +%T) wide extraction finished"
