"""Multi-day holding tests on a daily closing-price series (rules fixed in README before running).

Input: a FRED CSV (observation_date,VALUE). A signal seen at day t's close is traded at day t+1's close.
Costs per side: commission (bot.FEE_RATE) + 0.02% slippage; SEC fee on sells. Cash earns nothing.
"""

import argparse
import csv
from datetime import date
import json
from pathlib import Path

from bot import FEE_RATE, SELL_FEE_RATE

SLIPPAGE = 0.0002


def load(path):
    rows = []
    with open(path, newline="") as handle:
        reader = csv.reader(handle)
        next(reader)
        for day, value in reader:
            if value and value != ".":
                rows.append((date.fromisoformat(day), float(value)))
    return rows


def sma(values, end, length):
    return sum(values[end - length + 1:end + 1]) / length if end + 1 >= length else None


def signals(closes, strategy):
    """Desired position (True = hold) decided at each day's close."""
    wanted, holding, held_days = [], False, 0
    for t, close in enumerate(closes):
        long_avg = sma(closes, t, 200)
        if strategy == "buy_and_hold":
            holding = True
        elif strategy == "trend_200":
            holding = long_avg is not None and close > long_avg
        elif strategy == "dip_in_uptrend":
            if holding:
                held_days += 1
                if close > sma(closes, t, 5) or held_days >= 5:
                    holding = False
            elif long_avg is not None and t >= 4 and close > long_avg and close == min(closes[t - 4:t + 1]):
                holding, held_days = True, 0
        elif strategy == "breakout_50":
            if holding:
                if t >= 20 and close < min(closes[t - 20:t]):
                    holding = False
            elif t >= 50 and close >= max(closes[t - 49:t + 1]):
                holding = True
        wanted.append(holding)
    return wanted


def backtest(rows, strategy, start, end):
    closes = [value for _, value in rows]
    wanted = signals(closes, strategy)  # indicators use the full history before `start`
    days = [i for i, (day, _) in enumerate(rows) if start <= day <= end]
    equity, peak, max_drawdown = 1.0, 1.0, 0.0
    invested, entry_value, trades, in_market = False, None, [], 0
    for i in days[1:]:
        if invested:
            equity *= closes[i] / closes[i - 1]
            in_market += 1
        want = wanted[i - 1]  # yesterday's signal, executed at today's close
        if want and not invested:
            equity *= 1 - FEE_RATE - SLIPPAGE
            invested, entry_value = True, equity
        elif not want and invested:
            equity *= 1 - FEE_RATE - SLIPPAGE - SELL_FEE_RATE
            invested = False
            trades.append(equity / entry_value * (1 - FEE_RATE - SLIPPAGE) - 1)
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, 1 - equity / peak)
    if invested:  # value the open position as if sold on the last day
        equity *= 1 - FEE_RATE - SLIPPAGE - SELL_FEE_RATE
        trades.append(equity / entry_value * (1 - FEE_RATE - SLIPPAGE) - 1)
    years = (rows[days[-1]][0] - rows[days[0]][0]).days / 365.25
    return {"strategy": strategy, "period": f"{rows[days[0]][0]}~{rows[days[-1]][0]}",
            "cagr_pct": round((equity ** (1 / years) - 1) * 100, 2), "final_multiple": round(equity, 2),
            "max_drawdown_pct": round(max_drawdown * 100, 1), "trades": len(trades),
            "win_rate_pct": round(100 * sum(t > 0 for t in trades) / len(trades), 1) if trades else None,
            "avg_trade_pct": round(100 * sum(trades) / len(trades), 2) if trades else None,
            "time_in_market_pct": round(100 * in_market / max(1, len(days) - 1), 1)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path)
    args = parser.parse_args()
    rows = load(args.csv)
    periods = [(date(1990, 1, 1), date(2009, 12, 31)), (date(2010, 1, 1), date(2100, 1, 1))]
    results = [backtest(rows, strategy, start, end) for start, end in periods
               for strategy in ("buy_and_hold", "trend_200", "dip_in_uptrend", "breakout_50")]
    print(json.dumps(results, indent=1))
