#!/bin/sh
# Sequential resumable download of Nasdaq ITCH 5.0 samples, verified by md5 or size + gzip test.
cd /d/us-stock-data/itch || exit 1
BASE="https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH"
for f in 12302019.NASDAQ_ITCH50.gz 07302019.NASDAQ_ITCH50.gz 10302019.NASDAQ_ITCH50.gz S101819-v50.txt.gz 08302019.NASDAQ_ITCH50.gz 01302019.NASDAQ_ITCH50.gz 03272019.NASDAQ_ITCH50.gz; do
  [ -f "$f.ok" ] && continue
  echo "$(date +%T) start $f"
  curl -s -S -L -C - --retry 20 --retry-delay 10 -o "$f" "$BASE/$f" || { echo "$(date +%T) FAILED $f"; continue; }
  # The server lists .md5sum files but returns 404 for them, so fall back to size + full gzip test.
  if curl -s -f -L -o "$f.md5sum" "$BASE/$f.md5sum"; then
    want=$(tr -d '\r' < "$f.md5sum" | awk '{print $1}')
    have=$(md5sum "$f" | awk '{print $1}')
    if [ "$want" = "$have" ]; then echo ok > "$f.ok"; echo "$(date +%T) verified $f"; else echo "$(date +%T) MD5 MISMATCH $f $want $have"; fi
  else
    rm -f "$f.md5sum"
    want=$(curl -s -I -L "$BASE/$f" | tr -d '\r' | awk 'tolower($1)=="content-length:"{n=$2} END{print n}')
    have=$(stat -c %s "$f")
    if [ -n "$want" ] && [ "$want" = "$have" ] && gzip -t "$f"; then
      echo size+gzip > "$f.ok"; echo "$(date +%T) verified $f (no md5; size $have and gzip test ok)"
    else
      echo "$(date +%T) CHECK FAILED $f size $have expected $want"
    fi
  fi
done
echo "$(date +%T) all done"
