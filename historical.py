"""Compare two scanners on historical LOBSTER level-one data."""

import argparse
from collections import defaultdict, deque
import csv
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from bot import Bar, Market, PaperBot, Scanner
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
        if minute is not None:
            process()
    return bars, daily_windows, (windows, *checks)


def run(folder, *, offset=-4, intraday_reference=False):
    batches = defaultdict(list)
    references = defaultdict(lambda: defaultdict(list))
    counts = [0, 0, 0, 0, 0]
    sessions = []
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
        bars, windows, stats = load_day(
            path, symbol, session, offset, None if intraday else references[symbol]
        )
        sessions.append({"date": session.isoformat(), "symbol": symbol, "bars": len(bars),
                         "evaluated_windows": stats[0]})
        for minute, window in windows.items():
            references[symbol][minute].append(window)
        for bar in bars:
            batches[bar.time].append(bar)
        counts = [total + value for total, value in zip(counts, stats)]

    market = Market(False, False, 0)
    bots = [PaperBot("breakout"), PaperBot("breakout_with_pressure")]
    scanners = [Scanner(), Scanner(require_pressure=True)]
    for _, bars in sorted(batches.items()):
        for scanner, bot in zip(scanners, bots):
            signals = {}
            for bar in bars:
                signal = scanner.update(bar)
                if signal:
                    signals[bar.symbol] = signal
            bot.step(bars, market, signals)
    # Breakout trades the pressure variant did not take: their PnL is what the filter avoided or missed.
    taken = {(symbol, opened) for symbol, opened, _ in bots[1].trades}
    skipped = [pnl for symbol, opened, pnl in bots[0].trades if (symbol, opened) not in taken]
    return {"reference": "intraday" if intraday else "prior_sessions_same_minute",
            "filter_avoided_loss": round(sum(min(pnl, 0) for pnl in skipped), 2),
            "filter_missed_profit": round(sum(max(pnl, 0) for pnl in skipped), 2),
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
    args = parser.parse_args()
    print(json.dumps(run(args.folder, offset=args.utc_offset, intraday_reference=args.intraday_reference), indent=2))



