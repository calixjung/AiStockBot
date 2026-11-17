"""Multi-day holding tests on individual US stocks with daily bars (rules fixed in README before running).

Data: defeatbeta/yahoo-finance-data `stock_prices.parquet` and `stock_profile.parquet` (split-adjusted, no
dividends, delisted names before 2024 missing). Needs pyarrow, numpy, pandas.
Signals use the day's close; orders fill at the next trading day's open. Fractional shares are used because
split-adjusted historical prices are not the prices of the time.
"""

import argparse
import json
from math import sqrt
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from bot import FEE_RATE, SELL_FEE_RATE

SLIPPAGE = 0.0005
BUY_COST = 1 + FEE_RATE + SLIPPAGE          # paid per dollar of stock bought at the open
SELL_KEEP = 1 - FEE_RATE - SLIPPAGE - SELL_FEE_RATE
START_CASH = 10_000.0
PERIODS = [("2005-01-01", "2015-12-31"), ("2016-01-01", "2026-12-31")]


def load(folder):
    table = pq.read_table(folder / "stock_prices.parquet",
                          filters=[("report_date", ">=", "2003-01-01")])
    for name in ("open", "high", "low", "close"):
        table = table.set_column(table.schema.get_field_index(name), name, pc.cast(table[name], pa.float64()))
    frame = table.to_pandas()
    frame["report_date"] = pd.to_datetime(frame["report_date"])
    frame = frame.drop_duplicates(["symbol", "report_date"], keep="last")
    profile = pq.read_table(folder / "stock_profile.parquet", columns=["symbol", "sector"]).to_pandas()
    stocks = set(profile.loc[profile["sector"].fillna("").str.len() > 0, "symbol"])
    calendar = frame.loc[frame["symbol"] == "SPY", "report_date"].sort_values().to_numpy()
    spy = frame[frame["symbol"] == "SPY"].set_index("report_date").reindex(calendar)
    frame = frame[frame["symbol"].isin(stocks)]
    return frame, calendar, spy


def universes(frame, calendar, size=100):
    """{first trading day index of a month: symbols ranked by the prior 60 days' median dollar volume}."""
    frame = frame.assign(dollars=(frame["close"] * frame["volume"]).astype("float32"))
    dollars = frame.pivot(index="report_date", columns="symbol", values="dollars").reindex(calendar)
    close = frame.pivot(index="report_date", columns="symbol", values="close").reindex(calendar)
    symbols = dollars.columns.to_numpy()
    values, closes = dollars.to_numpy(), close.to_numpy()
    months = pd.DatetimeIndex(calendar).to_period("M")
    result = {}
    for i in range(60, len(calendar)):
        if months[i] == months[i - 1]:
            continue
        window = values[i - 60:i]
        enough = np.sum(~np.isnan(window), axis=0) >= 40
        with np.errstate(all="ignore"):
            med = np.nanmedian(window, axis=0)
        ok = enough & (closes[i - 1] >= 5) & ~np.isnan(med)
        order = np.argsort(-np.where(ok, med, -1))[:size]
        result[i] = [symbols[j] for j in order if ok[j]]
    return result


def indicators(frame, calendar, names):
    part = frame[frame["symbol"].isin(names)]
    wide = {field: part.pivot(index="report_date", columns="symbol", values=field).reindex(calendar)
            for field in ("open", "high", "low", "close", "volume")}
    close, volume = wide["close"], wide["volume"]
    change = close.diff()
    gain = change.clip(lower=0).ewm(alpha=0.5, adjust=False).mean()
    loss = (-change.clip(upper=0)).ewm(alpha=0.5, adjust=False).mean()
    rsi2 = 100 - 100 / (1 + gain / loss.replace(0, np.nan))
    rsi2 = rsi2.where(loss > 0, 100.0).where(change.notna())
    sma5, sma200 = close.rolling(5).mean(), close.rolling(200).mean()
    signals = {
        "trend_55_20": (close >= close.rolling(55).max(), close < close.rolling(20).min().shift(1), None),
        "dip_in_uptrend": ((close > sma200) & (rsi2 < 10), close > sma5, 10),
        "volume_flat": ((volume >= 3 * volume.rolling(20).median().shift(1))
                        & ((close / close.shift(1) - 1).abs() <= 0.005)
                        & (close >= (wide["high"] + wide["low"]) / 2), None, 5),
    }
    day_range = wide["high"] - wide["low"]
    signals["surge_follow"] = (
        (close / close.shift(1) - 1 >= 0.05) & (volume >= 3 * volume.rolling(20).median().shift(1))
        & (close >= wide["low"] + 0.75 * day_range) & (close > sma200),
        close < close.rolling(50).mean(), 40)
    momentum = close.shift(21) / close.shift(252) - 1
    uptrend = (momentum > 0) & (close > sma200)
    return wide, signals, momentum, uptrend


def simulate(wide, calendar, spy, universe, start, end, entry, exit_signal, max_hold, *, slots=10,
             momentum=None, eligible=None, risk_on=None, detail=None):
    # detail: pass a dict to keep positions open at the end and receive the full state (paper trading).
    opens, closes = wide["open"].to_numpy(), wide["close"].to_numpy()
    column = {symbol: j for j, symbol in enumerate(wide["close"].columns)}
    entry_values = entry.to_numpy() if entry is not None else None
    exit_values = exit_signal.to_numpy() if exit_signal is not None else None
    momentum_values = momentum.to_numpy() if momentum is not None else None
    eligible_values = eligible.to_numpy() if eligible is not None else None
    spy_open = spy["open"].to_numpy()
    days = np.where((calendar >= np.datetime64(start)) & (calendar <= np.datetime64(end)))[0]
    month_starts = sorted(universe)
    cash, positions, trades, curve = START_CASH, {}, [], []
    pending_sell, pending_buy, last_close, members, exposure = set(), [], {}, [], 0

    def close_position(symbol, price, day):
        nonlocal cash
        shares, cost, opened = positions.pop(symbol)
        proceeds = shares * price * SELL_KEEP
        cash += proceeds
        market = spy_open[day] / spy_open[opened] - 1 if day > opened else 0.0
        trades.append((proceeds / cost - 1, proceeds / cost - 1 - market, day - opened))
        if detail is not None:
            detail.setdefault("closed", []).append(
                {"symbol": symbol, "bought": str(calendar[opened])[:10], "sold": str(calendar[day])[:10],
                 "return_pct": round((proceeds / cost - 1) * 100, 2)})

    for t in days:
        # --- open: orders decided at yesterday's close ---
        for symbol in list(pending_sell):
            price = opens[t, column[symbol]]
            if not np.isnan(price):
                close_position(symbol, price * 1.0, t)
                pending_sell.discard(symbol)
        if pending_buy:
            equity = cash + sum(shares * last_close[symbol] for symbol, (shares, _, _) in positions.items())
            for symbol in pending_buy:
                price = opens[t, column[symbol]]
                budget = min(equity / slots, cash)
                if np.isnan(price) or budget < 50 or len(positions) >= slots or symbol in positions:
                    continue
                shares = budget / (price * BUY_COST)
                cash -= budget
                positions[symbol] = (shares, budget, t)
                last_close[symbol] = price
            pending_buy = []
        # --- close: mark, then decide tomorrow's orders ---
        for symbol in positions:
            price = closes[t, column[symbol]]
            if not np.isnan(price):
                last_close[symbol] = price
        equity = cash + sum(shares * last_close[symbol] for symbol, (shares, _, _) in positions.items())
        curve.append(equity)
        exposure += len(positions) / slots
        if t in universe:
            members = universe[t]
        elif not members:
            earlier = [i for i in month_starts if i <= t]
            members = universe[earlier[-1]] if earlier else []
        if momentum_values is not None:
            if t in universe:
                ranked = sorted(((momentum_values[t, column[s]], s) for s in members
                                 if not np.isnan(momentum_values[t, column[s]])
                                 and (eligible_values is None or bool(eligible_values[t, column[s]]))),
                                reverse=True)[:slots]
                target = [symbol for _, symbol in ranked]
                if risk_on is not None and not risk_on[t]:
                    target = []  # market below its 200-day average: cash this month
                pending_sell |= {symbol for symbol in positions if symbol not in target}
                pending_buy = [symbol for symbol in target if symbol not in positions]
            continue
        for symbol, (_, _, opened) in positions.items():
            j = column[symbol]
            leave = exit_values is not None and bool(exit_values[t, j])
            if leave or (max_hold is not None and t - opened + 1 >= max_hold):
                pending_sell.add(symbol)
        free = slots - (len(positions) - len(pending_sell))
        if free > 0:
            pending_buy = [symbol for symbol in members
                           if bool(entry_values[t, column[symbol]]) and symbol not in positions][:free]
    last = days[-1]
    if detail is not None:
        detail.update(
            cash=cash, curve=[float(value) for value in curve], dates=[str(day)[:10] for day in calendar[days]],
            positions=[{"symbol": symbol, "bought": str(calendar[opened])[:10], "cost": cost,
                        "value": shares * last_close[symbol], "last_close": float(last_close[symbol])}
                       for symbol, (shares, cost, opened) in positions.items()],
            pending_sell=sorted(pending_sell), pending_buy=list(pending_buy), members=list(members))
        return summary(curve, calendar[days], trades, exposure / len(days))
    for symbol in list(positions):
        close_position(symbol, last_close[symbol], last)
    curve[-1] = cash
    return summary(curve, calendar[days], trades, exposure / len(days))


def equal_weight(wide, calendar, universe, start, end):
    opens, closes = wide["open"].to_numpy(), wide["close"].to_numpy()
    column = {symbol: j for j, symbol in enumerate(wide["close"].columns)}
    days = np.where((calendar >= np.datetime64(start)) & (calendar <= np.datetime64(end)))[0]
    shares = np.zeros(closes.shape[1])
    cash, curve, rebalance, last = START_CASH, [], None, np.zeros(closes.shape[1])
    first = True
    for t in days:
        if rebalance is not None:
            price = np.where(np.isnan(opens[t]), last, opens[t])
            equity = cash + float(np.sum(shares * price))
            target = np.zeros_like(shares)
            valid = [column[s] for s in rebalance if price[column[s]] > 0]
            for j in valid:
                target[j] = equity * 0.99 / len(valid) / price[j]
            delta = target - shares
            buys = float(np.sum(np.clip(delta, 0, None) * price))
            sells = float(np.sum(np.clip(-delta, 0, None) * price))
            cash += sells * SELL_KEEP - buys * BUY_COST
            shares, rebalance = target, None
        last = np.where(np.isnan(closes[t]), last, closes[t])
        curve.append(cash + float(np.sum(shares * last)))
        if t in universe or first:
            earlier = [i for i in universe if i <= t]
            rebalance = universe[earlier[-1]] if earlier else None
            first = False
    curve[-1] = cash + float(np.sum(shares * last)) * SELL_KEEP
    return summary(curve, calendar[days], [], 1.0)


def summary(curve, dates, trades, exposure):
    curve = np.array(curve)
    years = (dates[-1] - dates[0]) / np.timedelta64(1, "D") / 365.25
    peak = np.maximum.accumulate(curve)
    result = {"period": f"{str(dates[0])[:10]}~{str(dates[-1])[:10]}",
              "cagr_pct": round(((curve[-1] / START_CASH) ** (1 / years) - 1) * 100, 2),
              "final": round(float(curve[-1]), 0), "max_drawdown_pct": round(float(np.max(1 - curve / peak)) * 100, 1),
              "exposure_pct": round(exposure * 100, 1), "trades": len(trades)}
    years_index = pd.DatetimeIndex(dates).year
    year_end = pd.Series(curve, index=years_index).groupby(level=0).last()
    previous = year_end.shift(1).fillna(START_CASH)
    result["yearly_pct"] = {int(year): round(float(value), 1) for year, value in ((year_end / previous - 1) * 100).items()}
    if trades:
        net = np.array([t[0] for t in trades]) * 100
        excess = np.array([t[1] for t in trades]) * 100
        result.update(win_rate_pct=round(float(np.mean(net > 0)) * 100, 1), avg_trade_pct=round(float(net.mean()), 2),
                      median_trade_pct=round(float(np.median(net)), 2),
                      avg_vs_spy_pct=round(float(excess.mean()), 2),
                      vs_spy_t=round(float(excess.mean() / (excess.std(ddof=1) / sqrt(len(excess)))), 2) if len(excess) > 1 else None,
                      avg_hold_days=round(float(np.mean([t[2] for t in trades])), 1))
    return result


def spy_hold(spy, calendar, start, end):
    days = np.where((calendar >= np.datetime64(start)) & (calendar <= np.datetime64(end)))[0]
    shares = START_CASH / (spy["open"].to_numpy()[days[0]] * BUY_COST)
    curve = shares * spy["close"].to_numpy()[days]
    curve[-1] *= SELL_KEEP
    return summary(curve, calendar[days], [], 1.0)


def monthly_saving(spy, calendar, start, end, amount=500.0):
    """Buy `amount` of SPY at the open after each month's first trading day; never sell."""
    days = np.where((calendar >= np.datetime64(start)) & (calendar <= np.datetime64(end)))[0]
    months = pd.DatetimeIndex(calendar).to_period("M")
    opens, closes = spy["open"].to_numpy(), spy["close"].to_numpy()
    shares = paid = 0.0
    worst = 0.0
    for position, t in enumerate(days[1:], start=1):
        previous = days[position - 1]
        if previous == days[0] or months[previous] != months[previous - 1]:
            shares += amount / (opens[t] * BUY_COST)
            paid += amount
        if paid:
            worst = min(worst, shares * closes[t] / paid - 1)
    value = shares * closes[days[-1]]
    return {"period": f"{str(calendar[days[0]])[:10]}~{str(calendar[days[-1]])[:10]}", "monthly_amount": amount,
            "paid_in": round(paid, 0), "value": round(float(value), 0),
            "gain_pct": round(float(value / paid - 1) * 100, 1),
            "worst_vs_paid_in_pct": round(float(worst) * 100, 1)}


def run(folder):
    frame, calendar, spy = load(folder)
    universe = universes(frame, calendar)
    names = sorted({symbol for members in universe.values() for symbol in members})
    wide, signals, momentum, uptrend = indicators(frame, calendar, names)
    risk_on = (spy["close"] > spy["close"].rolling(200).mean()).to_numpy()
    results = {"universe_symbols_ever": len(names), "periods": {}}
    for start, end in PERIODS:
        block = {"spy_buy_and_hold": spy_hold(spy, calendar, start, end),
                 "universe_equal_weight": equal_weight(wide, calendar, universe, start, end)}
        for name, (entry, exit_signal, max_hold) in signals.items():
            block[name] = simulate(wide, calendar, spy, universe, start, end, entry, exit_signal, max_hold)
        for name, options in (("momentum_monthly", {}), ("momentum_market_filter", {"risk_on": risk_on}),
                              ("momentum_stock_filter", {"eligible": uptrend}),
                              ("momentum_both_filters", {"risk_on": risk_on, "eligible": uptrend})):
            block[name] = simulate(wide, calendar, spy, universe, start, end, None, None, None,
                                   momentum=momentum, **options)
        results["periods"][f"{start[:4]}-{end[:4]}"] = block
    results["spy_monthly_saving"] = [monthly_saving(spy, calendar, start, "2026-12-31")
                                     for start in ("2005-01-01", "2016-01-01")]
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="Folder with stock_prices.parquet and stock_profile.parquet")
    args = parser.parse_args()
    print(json.dumps(run(args.folder), indent=1))
