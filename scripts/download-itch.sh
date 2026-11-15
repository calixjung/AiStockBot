#!/bin/sh
# Sequential resumable download of Nasdaq ITCH 5.0 samples, verified by md5 or size + gzip test.
# Each resume is a fresh curl started from the file's real size. Two copies of this script writing
# the same file once produced files larger than the server copy, so a lock folder blocks a second run
# (delete download.lock by hand only if no copy is running).
# Usage: sh download-itch.sh                      (the 2019 sample days into /d/us-stock-data/itch)
#        ITCH_DIR=/d/us-stock-data/itch-recent sh download-itch.sh FILE...   (any files from the server folder)
ITCH_DIR=${ITCH_DIR:-/d/us-stock-data/itch}
mkdir -p "$ITCH_DIR" && cd "$ITCH_DIR" || exit 1
mkdir download.lock 2>/dev/null || { echo "$(date +%T) another download is running (download.lock exists)"; exit 1; }
trap 'rmdir download.lock' EXIT
BASE="https://emi.nasdaq.com/ITCH/Nasdaq%20ITCH"
FILES=${*:-12302019.NASDAQ_ITCH50.gz 07302019.NASDAQ_ITCH50.gz 10302019.NASDAQ_ITCH50.gz S101819-v50.txt.gz 08302019.NASDAQ_ITCH50.gz 01302019.NASDAQ_ITCH50.gz 03272019.NASDAQ_ITCH50.gz}
for f in $FILES; do
  [ -f "$f.ok" ] && continue
  want=$(curl -s -I -L "$BASE/$f" | tr -d '\r' | awk 'tolower($1)=="content-length:"{n=$2} END{print n}')
  [ -n "$want" ] || { echo "$(date +%T) FAILED $f (no size from server)"; continue; }
  # Keep at least 20 GB free after this file so extraction still has room.
  free=$(df -k . | awk 'NR==2{print $4}')
  have_now=$(stat -c %s "$f" 2>/dev/null || echo 0)
  if [ $(( (want - have_now) / 1024 + 20 * 1024 * 1024 )) -gt "$free" ]; then
    echo "$(date +%T) LOW SPACE: stop before $f (free $((free / 1024 / 1024)) GB)"; break
  fi
  echo "$(date +%T) start $f ($want bytes)"
  ok=
  for attempt in 1 2; do
    tries=0
    while :; do
      have=$(stat -c %s "$f" 2>/dev/null || echo 0)
      if [ "$have" -gt "$want" ]; then echo "$(date +%T) oversize $f ($have), starting over"; rm -f "$f"; have=0; fi
      [ "$have" -eq "$want" ] && break
      tries=$((tries + 1))
      [ "$tries" -gt 100 ] && break
      curl -s -S -L -C - -o "$f" "$BASE/$f" || sleep 10
    done
    [ "$(stat -c %s "$f" 2>/dev/null || echo 0)" -eq "$want" ] || break
    # The server lists .md5sum files but returns 404 for them, so fall back to a full gzip test.
    if curl -s -f -L -o "$f.md5sum" "$BASE/$f.md5sum"; then
      [ "$(tr -d '\r' < "$f.md5sum" | awk '{print $1}')" = "$(md5sum "$f" | awk '{print $1}')" ] && ok=md5
    else
      rm -f "$f.md5sum"
      gzip -t "$f" && ok=size+gzip
    fi
    [ -n "$ok" ] && break
    echo "$(date +%T) corrupt $f on attempt $attempt, deleting"; rm -f "$f"
  done
  if [ -n "$ok" ]; then echo "$ok" > "$f.ok"; echo "$(date +%T) verified $f ($ok)"; else echo "$(date +%T) CHECK FAILED $f"; fi
done
echo "$(date +%T) all done"
