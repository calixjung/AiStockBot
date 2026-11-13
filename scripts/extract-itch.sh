#!/bin/sh
# Wait for each verified download, then extract replay CSVs; SPY/QQQ go to index/ so they are not traded.
cd /d/us-stock-data/itch || exit 1
SYMS="AAPL AMZN GOOG INTC MSFT TSLA NVDA AMD FB NFLX SPY QQQ"
for pair in 12302019.NASDAQ_ITCH50.gz:2019-12-30 07302019.NASDAQ_ITCH50.gz:2019-07-30 10302019.NASDAQ_ITCH50.gz:2019-10-30 S101819-v50.txt.gz:2019-10-18 08302019.NASDAQ_ITCH50.gz:2019-08-30 01302019.NASDAQ_ITCH50.gz:2019-01-30 03272019.NASDAQ_ITCH50.gz:2019-03-27; do
  f=${pair%%:*}; d=${pair##*:}
  [ -d "/d/us-stock-data/sessions/$d" ] && continue
  while [ ! -f "$f.ok" ]; do
    grep -q "FAILED $f\|MISMATCH $f" download.log 2>/dev/null && { echo "$(date +%T) skip $f (download failed)"; continue 2; }
    sleep 30
  done
  echo "$(date +%T) extract $f -> $d"
  python -I -B /d/us-stock-bot/itch_extract.py "$f" /d/us-stock-data/sessions "$d" $SYMS || { echo "$(date +%T) EXTRACT FAILED $d"; continue; }
  mkdir -p "/d/us-stock-data/index/$d"
  mv "/d/us-stock-data/sessions/$d/SPY" "/d/us-stock-data/sessions/$d/QQQ" "/d/us-stock-data/index/$d/"
  echo "$(date +%T) done $d"
done
echo "$(date +%T) extraction finished"
