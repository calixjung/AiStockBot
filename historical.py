"""Compare two scanners on historical LOBSTER level-one data."""

import argparse
from collections import defaultdict, deque
import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from bot import Bar, Market, PaperBot, Scanner
from evaluate import baseline, beats_random, random_bots, trade_stats
from orb import OpeningRangeScanner
from pressure import PressureWindow, stalled_buying


def load_day(folder, symbol, session, offset, reference=None):
    base = datetime.combine(session, datetime.min.time(), timezone(timedelta(hours=offset)))
    bars = []
    daily_windows = {}
    recent = deque(maxlen=15)
    history = []
    minute = None
    previous_quote = None
    trades = []
    mids = []
    checks = [0, 0, 0, 0]
    windows = 0
    opening = None

    def process():
        nonlocal windows
        if not trades:
            recent.clear()
            return
        timestamp = base + timedelta(minutes=minute + 1)
        prices = [price for price, _, _ in trades]
        volume = sum(size for _, size, _ in trades)
        buy = sum(price * size for price, size, side in trades if side == -1)
        sell = sum(price * size for price, size, side in trades if side == 1)
        recent.append((volume, buy, sell, min(mids) if mids else None, max(mids) if mids else None))
        ratio = None
        pressure = None
        if len(recent) == 15:
            values = [sum(item[index] for item in recent) for index in range(3)]
            daily_windows[minute] = (values[0], values[1] + values[2])
            # In multi-day mode the reference contains only completed earlier sessions.
            comparable = reference.get(minute, ()) if reference is not None else history[:-14]
            if comparable and (reference is not None or len(history) >= 30):
                typical_volume = median(item[0] for item in comparable)
                typical_dollars = median(item[1] for item in comparable)
                ratio = values[0] / typical_volume if typical_volume else None
                if all(item[3] is not None for item in recent):
                    pressure = PressureWindow(values[1], values[2], typical_dollars,
                                              max(item[4] for item in recent),
                                              min(item[3] for item in recent))
                    windows += 1
                    checks[0] += int(ratio is not None and ratio >= 3)
                    pressure_passed = stalled_buying(pressure)
                    checks[1] += int(pressure_passed)
                    if ratio is not None and ratio >= 3:
                        classified = values[1] + values[2]
                        checks[2] += int(classified > 0 and values[1] / classified >= 0.70)
                        checks[3] += int(pressure.high_mid <= pressure.low_mid * 1.005)
            history.append((values[0], values[1] + values[2]))
        bid, ask, quote_seconds = previous_quote or (None, None, 0)
        bar = Bar(timestamp, symbol, max(prices), min(prices), prices[-1], volume,
                  ratio, bid, ask, (minute + 1) * 60 - quote_seconds, pressure=pressure)
        bars.append(bar)

    with (folder / "message.csv").open(newline="") as messages, (folder / "orderbook.csv").open(newline="") as quotes:
        for message, quote in zip(csv.reader(messages), csv.reader(quotes), strict=True):
            seconds = float(message[0])
            event_minute = int(seconds // 60)
            if not 570 <= event_minute < 960:
                continue
            if event_minute != minute:
                if minute is not None:
                    process()
                    if event_minute != minute + 1:
                        recent.clear()
                minute = event_minute
                trades = []
                mids = []
            ask, bid = int(quote[0]) / 10_000, int(quote[2]) / 10_000
            if 0 < bid < ask < 100_000:
                previous_quote = (bid, ask, seconds)
                mids.append((ask + bid) / 2)
            else:
                previous_quote = None
            if message[1] in {"4", "5"} and message[5] in {"-1", "0", "1"}:
                trades.append((int(message[4]) / 10_000, int(message[3]), int(message[5])))
                if opening is None:
                    opening = trades[-1][0]
        if minute is not None:
            process()
    return bars, daily_windows, (windows, *checks), opening


def load_index(folder, session, offset):
    """Last trade price and session VWAP at the end of each regular-session minute."""
    base = datetime.combine(session, datetime.min.time(), timezone(timedelta(hours=offset)))
    states = {}
    value = volume = 0.0
    last = None
    minute = None
    with (folder / "message.csv").open(newline="") as messages:
        for message in csv.reader(messages):
            event_minute = int(float(message[0]) // 60)
            if not 570 <= event_minute < 960:
                continue
            if minute is not None and event_minute != minute and last is not None:
                states[base + timedelta(minutes=minute + 1)] = (last, value / volume)
            minute = event_minute
            if message[1] in {"4", "5"}:
                price, size = int(message[4]) / 10_000, int(message[3])
                value += price * size
                volume += size
                last = price
    if minute is not None and last is not None:
        states[base + timedelta(minutes=minute + 1)] = (last, value / volume)
    return states


def market_states(index_folder, days, offset):
    """SPY/QQQ below session VWAP per minute end, forward-filled; None where data is missing."""
    states = {}
    for day in days:
        series = []
        for symbol in ("SPY", "QQQ"):
            path = index_folder / day.isoformat() / symbol if index_folder else None
            series.append(load_index(path, day, offset) if path and (path / "message.csv").is_file() else None)
        if None in series:
            continue
        latest = [None, None]
        for timestamp in sorted(set(series[0]) | set(series[1])):
            for position, data in enumerate(series):
                if timestamp in data:
                    last, vwap = data[timestamp]
                    latest[position] = last < vwap
            if None not in latest:
                states[timestamp] = tuple(latest)
    return states


def compare(base, other):
    # Trades the base variant took and the other did not: what the extra filter avoided or missed.
    taken = {(symbol, opened) for symbol, opened, *_ in other.trades}
    skipped = [pnl for symbol, opened, pnl, _ in base.trades if (symbol, opened) not in taken]
    return round(sum(min(pnl, 0) for pnl in skipped), 2), round(sum(max(pnl, 0) for pnl in skipped), 2)


def run(folder, *, offset=-4, intraday_reference=False, index_folder=None, random_runs=20):
    batches = defaultdict(list)
    references = defaultdict(lambda: defaultdict(list))
    counts = [0, 0, 0, 0, 0]
    sessions = []
    openings = {}
    if (folder / "message.csv").is_file():
        inputs = [(date(2012, 6, 21), "AAPL", folder)]
        intraday = True
    else:
        inputs = [
            (date.fromisoformat(day.name), symbol.name, symbol)
            for day in sorted(folder.iterdir()) if day.is_dir()
            for symbol in sorted(day.iterdir()) if symbol.is_dir()
        ]
        intraday = intraday_reference
    if not inputs:
        raise ValueError("No data found. Expected YYYY-MM-DD/SYMBOL/message.csv and orderbook.csv.")
    for session, symbol, path in inputs:
        bars, windows, stats, opening = load_day(
            path, symbol, session, offset, None if intraday else references[symbol]
        )
        openings[session, symbol] = opening
        sessions.append({"date": session.isoformat(), "symbol": symbol, "bars": len(bars),
                         "evaluated_windows": stats[0]})
        for minute, window in windows.items():
            references[symbol][minute].append(window)
        for bar in bars:
            batches[bar.time].append(bar)
        counts = [total + value for total, value in zip(counts, stats)]

    days = sorted({session for session, _, _ in inputs})
    if index_folder is None and folder.name == "sessions" and (folder.parent / "index").is_dir():
        index_folder = folder.parent / "index"
    states = market_states(index_folder, days, offset)
    covered = sorted({timestamp.date().isoformat() for timestamp in states})
    # (variant, signal source, market veto); signal sources: 0 breakout, 1 breakout+pressure, 2 ORB.
    variants = [("breakout", 0, False), ("breakout_with_pressure", 1, False)]
    if covered:
        variants += [("breakout_market_veto", 0, True), ("pressure_market_veto", 1, True)]
    variants.append(("orb", 2, False))
    if covered:
        variants.append(("orb_market_veto", 2, True))
    bots = [PaperBot(name, market_filter=veto) for name, _, veto in variants]
    named = {bot.name: bot for bot in bots}
    scanners = [Scanner(), Scanner(require_pressure=True)]
    orb = OpeningRangeScanner(openings)
    randoms, random_scanners = random_bots(random_runs)
    closes = {}
    weak_minutes = 0
    for timestamp, bars in sorted(batches.items()):
        for bar in bars:
            closes[timestamp.date(), bar.symbol] = bar.close
        # Declining = last price below the session's first regular trade, among symbols traded so far.
        traded = [close < openings[session, symbol]
                  for (session, symbol), close in closes.items() if session == timestamp.date()]
        spy_weak, qqq_weak = states.get(timestamp, (False, False))
        market = Market(spy_weak, qqq_weak, sum(traded) / len(traded))
        weak_minutes += market.buy_veto() is not None and timestamp in states
        signals = []
        for scanner in scanners:
            found = {}
            for bar in bars:
                signal = scanner.update(bar)
                if signal:
                    found[bar.symbol] = signal
            signals.append(found)
        signals.append(orb.update(bars))
        for (_, source, _), bot in zip(variants, bots):
            bot.step(bars, market, signals[source])
        for bot, scanner in zip(randoms, random_scanners):
            bot.step(bars, market, scanner.update(bars))
    pressure_avoided, pressure_missed = compare(named["breakout"], named["breakout_with_pressure"])
    market_avoided, market_missed = (compare(named["breakout"], named["breakout_market_veto"])
                                     if covered else (None, None))
    orb_avoided, orb_missed = compare(named["orb"], named["orb_market_veto"]) if covered else (None, None)
    # Early/late split over the evaluated days (the first day only builds the reference).
    evaluated = days[1:] if not intraday and len(days) > 1 else days
    split = evaluated[len(evaluated) // 2] if len(evaluated) > 1 else None
    random_summary = baseline(randoms)
    evaluation = {}
    for bot in bots:
        stats = trade_stats(bot.trades, split)
        stats["random_share_at_least_as_good"] = beats_random(stats.get("mean_bp"), random_summary.get("means", []))
        evaluation[bot.name] = stats
    return {"reference": "intraday" if intraday else "prior_sessions_same_minute",
            "filter_avoided_loss": pressure_avoided, "filter_missed_profit": pressure_missed,
            "market_filter_dates": covered, "weak_market_minutes": weak_minutes,
            "market_avoided_loss": market_avoided, "market_missed_profit": market_missed,
            "orb_selections": orb.selections,
            "evaluation": evaluation, "split_date": split.isoformat() if split else None,
            "random_baseline": random_summary,
            "orb_market_avoided_loss": orb_avoided, "orb_market_missed_profit": orb_missed,
            "sessions": sessions, "windows": counts[0],
            "relative_volume_pass": counts[1], "pressure_pass": counts[2],
            "buy_share_among_volume_pass": counts[3],
            "tight_mid_among_volume_pass": counts[4],
            "summary": [bot.report() for bot in bots]}


if __name__ == "__main__":
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("--utc-offset", type=int, choices=(-5, -4), default=-4,
                        help="Offset for the input dates; group standard and daylight time separately.")
    parser.add_argument("--intraday-reference", action="store_true",
                        help="Smoke test using earlier non-overlapping windows on the same day.")
    parser.add_argument("--index", type=Path,
                        help="YYYY-MM-DD/SPY|QQQ folder for the market veto (default: ../index next to sessions)")
    args = parser.parse_args()
    print(json.dumps(run(args.folder, offset=args.utc_offset, intraday_reference=args.intraday_reference,
                         index_folder=args.index), indent=2))



