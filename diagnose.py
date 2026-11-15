"""Why do the paper trades lose? Split each round trip into price move, spread and commission.

For every paper_buy/paper_sell pair in a dashboard (or historical JSON) the mid-quote is looked up at the
entry and exit minute from the extracted data, so each trade's result becomes
    mid move (entry mid -> exit mid) - spread and slippage paid - commission (FEE_RATE each side).
The same entries are also followed with mid-quote changes 15, 30, 60, 120 minutes later and at 15:55
("what if it had been held"), to see whether the entries are wrong or the exits cut them short.
"""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta
import json
from pathlib import Path
from statistics import mean, median

from bot import FEE_RATE, SELL_FEE_RATE
from fees import load
from news_study import mids_by_minute

HORIZONS = (15, 30, 60, 120)


def pairs(events):
    open_buys, trips = {}, []
    for event in events:
        key = event["variant"], event["symbol"]
        if event["event"] == "paper_buy":
            open_buys[key] = event
        elif event["event"] == "paper_sell" and key in open_buys:
            trips.append((open_buys.pop(key), event))
    return trips


def summarize(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"n": len(values), "mean_bp": round(mean(values), 1), "median_bp": round(median(values), 1),
            "up_pct": round(100 * sum(v > 0 for v in values) / len(values), 1)}


def run(source, sessions, offset):
    trips = pairs(load(source)["events"])
    mids = {}
    rows = defaultdict(list)
    for buy, sell in trips:
        entry = datetime.fromisoformat(buy["time"])
        key = entry.date(), buy["symbol"]
        if key not in mids:
            mids[key] = mids_by_minute(sessions / entry.date().isoformat() / buy["symbol"], buy["symbol"], entry.date(), offset)
        path = mids[key]
        start = entry.replace(tzinfo=None)
        exit_time = datetime.fromisoformat(sell["time"]).replace(tzinfo=None)
        entry_mid, exit_mid = path.get(start), path.get(exit_time)
        if not entry_mid or not exit_mid:
            continue
        move = (exit_mid / entry_mid - 1) * 10_000
        spread = ((buy["price"] / entry_mid - 1) + (1 - sell["price"] / exit_mid)) * 10_000
        commission = (2 * FEE_RATE + SELL_FEE_RATE) * 10_000
        close = start.replace(hour=15, minute=55)
        later = {f"{m}m": ((path.get(start + timedelta(minutes=m)) or 0) / entry_mid - 1) * 10_000
                 if path.get(start + timedelta(minutes=m)) else None for m in HORIZONS}
        later["close"] = (path[close] / entry_mid - 1) * 10_000 if path.get(close) else None
        window = [path[t] for t in sorted(path) if start < t <= start + timedelta(minutes=60)]
        rows[buy["variant"]].append({
            "reason": sell.get("reason"), "hold_min": (exit_time - start).total_seconds() / 60,
            "move": move, "spread": spread, "commission": commission, "net": move - spread - commission,
            "best_60m": (max(window) / entry_mid - 1) * 10_000 if window else None,
            "worst_60m": (min(window) / entry_mid - 1) * 10_000 if window else None, **later})
    report = {}
    for variant, items in sorted(rows.items()):
        by_reason = defaultdict(list)
        for item in items:
            by_reason[item["reason"]].append(item)
        report[variant] = {
            "trades": len(items),
            "average_bp": {part: round(mean(item[part] for item in items), 1)
                           for part in ("move", "spread", "commission", "net")},
            "median_hold_min": round(median(item["hold_min"] for item in items), 1),
            "by_exit_reason": {reason: {"trades": len(group), "move_bp": round(mean(i["move"] for i in group), 1),
                                        "hold_min": round(median(i["hold_min"] for i in group), 1)}
                               for reason, group in by_reason.items()},
            "if_held_mid_change": {h: summarize([item[h] for item in items]) for h in (*[f"{m}m" for m in HORIZONS], "close")},
            "best_in_60m": summarize([item["best_60m"] for item in items]),
            "worst_in_60m": summarize([item["worst_60m"] for item in items]),
        }
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Dashboard HTML or JSON with events")
    parser.add_argument("sessions", type=Path, help="Extracted sessions folder used for that run")
    parser.add_argument("--utc-offset", type=int, default=-5)
    args = parser.parse_args()
    print(json.dumps(run(args.source, args.sessions, args.utc_offset), indent=1, ensure_ascii=False))
