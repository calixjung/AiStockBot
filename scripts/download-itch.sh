#!/bin/sh
# Sequential resumable download of Nasdaq ITCH 5.0 samples with md5 check.
cd /d/us-stock-data/itch || exit 1
BASE="https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH"
for f in 12302019.NASDAQ_ITCH50.gz 07302019.NASDAQ_ITCH50.gz 10302019.NASDAQ_ITCH50.gz S101819-v50.txt.gz 08302019.NASDAQ_ITCH50.gz 01302019.NASDAQ_ITCH50.gz 03272019.NASDAQ_ITCH50.gz; do
  [ -f "$f.ok" ] && continue
  echo "$(date +%T) start $f"
  curl -s -S -L -C - --retry 20 --retry-delay 10 -o "$f" "$BASE/$f" || { echo "$(date +%T) FAILED $f"; continue; }
  if curl -s -S -L -o "$f.md5sum" "$BASE/$f.md5sum"; then
    want=$(tr -d '\r' < "$f.md5sum" | awk '{print $1}')
    have=$(md5sum "$f" | awk '{print $1}')
    if [ "$want" = "$have" ]; then echo ok > "$f.ok"; echo "$(date +%T) verified $f"; else echo "$(date +%T) MD5 MISMATCH $f $want $have"; fi
  else
    echo "$(date +%T) no md5 for $f"; echo nomd5 > "$f.ok"
  fi
done
echo "$(date +%T) all done"
