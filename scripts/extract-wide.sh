#!/bin/sh
# Extract a fixed symbol list (WIDE/symbols.txt, chosen from the first day of the period) for several days.
# Each day starts as soon as its download is verified (*.ok); days run in parallel.
# Output: WIDE/sessions (trading symbols) and WIDE/index (SPY, QQQ).
# Usage: sh extract-wide.sh                                   (2019 sample days)
#        WIDE=/d/us-stock-data/recent ITCH_DIR=/d/us-stock-data/itch-recent sh extract-wide.sh FILE:YYYY-MM-DD ...
# Requires Python on PATH.
WIDE=${WIDE:-/d/us-stock-data/wide}
ITCH_DIR=${ITCH_DIR:-/d/us-stock-data/itch}
cd "$ITCH_DIR" || exit 1
SYMS="$(tr '\r\n' '  ' < "$WIDE/symbols.txt") SPY QQQ"
PAIRS=${*:-01302019.NASDAQ_ITCH50.gz:2019-01-30 03272019.NASDAQ_ITCH50.gz:2019-03-27 07302019.NASDAQ_ITCH50.gz:2019-07-30 08302019.NASDAQ_ITCH50.gz:2019-08-30 S101819-v50.txt.gz:2019-10-18 10302019.NASDAQ_ITCH50.gz:2019-10-30 12302019.NASDAQ_ITCH50.gz:2019-12-30}
for pair in $PAIRS; do
  f=${pair%%:*}; d=${pair##*:}
  [ -d "$WIDE/sessions/$d" ] && continue
  # Wait for the download script to verify the file (or to give up on it).
  # The download script holds ITCH_DIR/download.lock while it runs; once it is gone, no more files will arrive.
  while [ ! -f "$f.ok" ]; do
    if [ ! -d "$ITCH_DIR/download.lock" ]; then
      echo "$(date +%T) skip $f (not downloaded or failed verification)"; continue 2
    fi
    sleep 60
  done
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
