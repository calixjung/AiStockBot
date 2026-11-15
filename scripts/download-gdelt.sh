#!/bin/sh
# Download GDELT 2.0 GKG (news articles with organizations, themes and tone) 15-minute files
# for US trading hours: 12:00-21:45 UTC covers 09:00-16:45 New York in both standard and daylight time.
# Free, no account. About 5-11 MB per file, ~40 files per day. Resumable; each file is checked with unzip -t.
# Usage: sh download-gdelt.sh YYYYMMDD ...
OUT=${GDELT_DIR:-/d/us-stock-data/gdelt}
BASE=https://data.gdeltproject.org/gdeltv2
for day in "$@"; do
  mkdir -p "$OUT/$day" && cd "$OUT/$day" || exit 1
  for hour in 12 13 14 15 16 17 18 19 20 21; do
    for minute in 00 15 30 45; do
      f="$day$hour${minute}00.gkg.csv.zip"
      [ -f "$f" ] && unzip -tq "$f" >/dev/null 2>&1 && continue
      curl -s -S -L -f --retry 5 --retry-delay 5 -o "$f.part" "$BASE/$f" && mv "$f.part" "$f" \
        || { rm -f "$f.part"; echo "$(date +%T) missing $f"; }
    done
  done
  echo "$(date +%T) done $day ($(ls *.zip 2>/dev/null | wc -l) files)"
done
echo "$(date +%T) gdelt download finished"
