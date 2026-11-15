"""Do GDELT news counts and tone come before price moves? (Hypotheses fixed in README before running.)

Timing: a GDELT 15-minute file stamped T (UTC) is treated as usable only from T + 30 minutes.
Evaluation points: every 15 minutes from 10:00 to 15:00 New York time; mid-quote changes 30 and 60 minutes later.
"""

import argparse
import csv
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import json
from math import sqrt
from pathlib import Path
from statistics import mean, median, stdev

from historical import load_day

LAG = timedelta(minutes=30)
WINDOW = timedelta(minutes=30)


def new_york_offset(day):
    """US daylight time: second Sunday of March to first Sunday of November."""
    march = date(day.year, 3, 8 + (6 - date(day.year, 3, 8).weekday()) % 7)
    november = date(day.year, 11, 1 + (6 - date(day.year, 11, 1).weekday()) % 7)
    return -4 if march <= day < november else -5


def load_news(agg_folder, day):
    """{key: [(usable_from as New York wall clock, articles, tone_sum)]}"""
    news = defaultdict(list)
    path = agg_folder / f"{day:%Y%m%d}.csv"
    if not path.is_file():
        return None
    shift = timedelta(hours=new_york_offset(day))
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            stamp = datetime.strptime(row["stamp"], "%Y%m%d%H%M%S") + shift + LAG
            news[row["key"]].append((stamp, int(row["articles"]), float(row["tone_sum"])))
    return news


def recent(items, end, span):
    articles = tone = 0
    for usable, count, tone_sum in items:
        if end - span < usable <= end:
            articles += count
            tone += tone_sum
    return articles, (tone / articles if articles else None)


def mids_by_minute(folder, symbol, day, offset):
    bars, *_ = load_day(folder, symbol, day, offset, {})
    return {bar.time.replace(tzinfo=None): (bar.bid + bar.ask) / 2
            for bar in bars if bar.bid and bar.ask and 0 < bar.bid <= bar.ask}


def change(mids, start, end):
    a, b = mids.get(start), mids.get(end)
    return (b / a - 1) * 10_000 if a and b else None


def stats(values):
    values = [v for v in values if v is not None]
    if not values:
        return {"n": 0}
    result = {"n": len(values), "mean_bp": round(mean(values), 2), "up_pct": round(100 * sum(v > 0 for v in values) / len(values), 1)}
    if len(values) > 1 and stdev(values) > 0:
        result["t"] = round(mean(values) / (stdev(values) / sqrt(len(values))), 2)
    return result


def grid(day):
    start = datetime.combine(day, datetime.min.time()) + timedelta(hours=10)
    return [start + timedelta(minutes=15 * step) for step in range(21)]  # 10:00 .. 15:00


def run(sessions, index, agg_folder, offset):
    days = sorted(date.fromisoformat(p.name) for p in sessions.iterdir() if p.is_dir())
    market_rows, company_rows, drop_rows = [], [], []
    earlier_tone, earlier_count = [], []
    for day in days:
        news = load_news(agg_folder, day)
        if news is None:
            continue
        spy = mids_by_minute(index / day.isoformat() / "SPY", "SPY", day, offset)
        for t in grid(day):
            count, tone = recent(news.get("MARKET", ()), t, WINDOW)
            if tone is None:
                continue
            spike = (len(earlier_tone) >= 20 and tone <= median(earlier_tone) - 1.0
                     and count >= median(earlier_count))
            market_rows.append((spike, change(spy, t, t + timedelta(minutes=30)), change(spy, t, t + timedelta(minutes=60))))
            earlier_tone.append(tone)
            earlier_count.append(count)
        for symbol_folder in sorted((sessions / day.isoformat()).iterdir()):
            symbol = symbol_folder.name
            mids = mids_by_minute(symbol_folder, symbol, day, offset)
            items = news.get(symbol, ())
            for t in grid(day):
                count, tone = recent(items, t, WINDOW)
                kind = ("negative" if count >= 2 and tone <= -2 else
                        "positive" if count >= 2 and tone >= 2 else "other")
                forward = [change(mids, t, t + timedelta(minutes=m)) for m in (30, 60)]
                spy_forward = [change(spy, t, t + timedelta(minutes=m)) for m in (30, 60)]
                adjusted = [f - s if f is not None and s is not None else None for f, s in zip(forward, spy_forward)]
                company_rows.append((kind, forward, adjusted))
                before, _ = recent(items, t - timedelta(minutes=15), timedelta(minutes=60))
                drop_rows.append((change(mids, t - timedelta(minutes=15), t), before > 0))
    market = {name: {"30m": stats([r[1] for r in market_rows if r[0] == flag]),
                     "60m": stats([r[2] for r in market_rows if r[0] == flag])}
              for name, flag in (("negative_spike", True), ("other", False))}
    company = {kind: {"30m": stats([r[1][0] for r in company_rows if r[0] == kind]),
                      "60m": stats([r[1][1] for r in company_rows if r[0] == kind]),
                      "30m_vs_spy": stats([r[2][0] for r in company_rows if r[0] == kind]),
                      "60m_vs_spy": stats([r[2][1] for r in company_rows if r[0] == kind])}
               for kind in ("negative", "positive", "other")}
    moves = sorted(r[0] for r in drop_rows if r[0] is not None)
    cutoff = moves[int(0.01 * len(moves))] if moves else None
    drops = [r for r in drop_rows if r[0] is not None and r[0] <= cutoff]
    every = [r for r in drop_rows if r[0] is not None]
    share = lambda rows: round(100 * sum(r[1] for r in rows) / len(rows), 1) if rows else None
    return {"days": [d.isoformat() for d in days], "market": market, "company": company,
            "drops": {"cutoff_bp": round(cutoff, 1) if cutoff is not None else None, "drop_windows": len(drops),
                      "news_before_drop_pct": share(drops), "news_before_any_window_pct": share(every)}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sessions", type=Path)
    parser.add_argument("--index", type=Path, help="Defaults to ../index next to sessions")
    parser.add_argument("--news", type=Path, default=Path("D:/us-stock-data/gdelt-agg"))
    parser.add_argument("--utc-offset", type=int, default=-5)
    args = parser.parse_args()
    print(json.dumps(run(args.sessions, args.index or args.sessions.parent / "index", args.news, args.utc_offset), indent=1))
