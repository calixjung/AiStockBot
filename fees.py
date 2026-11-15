"""Recompute paper trades under different brokerage fees.

Reads a generated dashboard (or a historical.py JSON with events), pairs each paper_buy with the next
paper_sell of the same strategy and symbol, and re-prices the round trip. Fill prices already include the
bid/ask spread and the 0.01% slippage of the replay; only the commission is changed:
    pnl = sell_value * (1 - rate) - fixed - (buy_value * (1 + rate) + fixed)
Budget and cash effects of a different fee are ignored (they are tiny at these sizes). Logged fill prices
never include the commission, so any run can be re-priced whatever fee it used.
"""

import argparse
import json
from math import sqrt
from pathlib import Path
from statistics import mean, stdev

SCENARIOS = [
    ("수수료 없음 (미국 무수수료 증권사 수준)", 0.0, 0.0),
    ("주문당 $1 (지금까지의 시험 기준)", 1.0, 0.0),
    ("한쪽 0.07% (국내 증권사 우대 수준)", 0.0, 0.0007),
    ("한쪽 0.25% (미래에셋 온라인, 봇 기본값)", 0.0, 0.0025),
]


def load(path):
    text = Path(path).read_text(encoding="utf-8")
    if text.lstrip().startswith("{"):
        return json.loads(text)
    start = text.index("} = {\"") + 4
    data, _ = json.JSONDecoder().raw_decode(text[start:])
    return data


def round_trips(events):
    open_buys, trips = {}, []
    for event in events:
        key = event["variant"], event["symbol"]
        if event["event"] == "paper_buy":
            open_buys[key] = event["quantity"] * event["price"]
        elif event["event"] == "paper_sell" and key in open_buys:
            trips.append((event["variant"], open_buys.pop(key), event["quantity"] * event["price"]))
    return trips


def table(trips, scenarios=SCENARIOS):
    result = {}
    for variant in sorted({trip[0] for trip in trips}):
        rows = [trip for trip in trips if trip[0] == variant]
        result[variant] = {}
        for label, fixed, rate in scenarios:
            pnls = [sell * (1 - rate) - fixed - (buy * (1 + rate) + fixed) for _, buy, sell in rows]
            returns = [pnl / (buy * (1 + rate) + fixed) * 10_000 for pnl, (_, buy, _) in zip(pnls, rows)]
            entry = {"trades": len(rows), "pnl": round(sum(pnls), 2), "mean_bp": round(mean(returns), 1),
                     "win_rate_pct": round(100 * sum(p > 0 for p in pnls) / len(pnls), 1)}
            if len(returns) > 1 and stdev(returns) > 0:
                entry["t_stat"] = round(mean(returns) / (stdev(returns) / sqrt(len(returns))), 2)
            result[variant][label] = entry
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="historical-*.html or JSON with events")
    args = parser.parse_args()
    for variant, rows in table(round_trips(load(args.source)["events"])).items():
        print(variant)
        for label, entry in rows.items():
            print(f"  {label}: {entry}")
