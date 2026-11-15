"""Event study: after heavy buying while the price stays flat, does the price rise?

For every 15-minute window that has a buy/sell classification (bar.pressure), record
- buy share: buy-initiated dollars / classified dollars,
- heavy: classified dollars >= 3x the usual for that minute (same reference as the scanner),
- flat: mid-quote range <= 0.5%,
and the mid-quote change 15, 30 and 60 minutes later. No trading rule, no costs: this only asks whether
the pattern carries information. Windows overlap (one per minute), so t values are optimistic; read them
as a rough guide and confirm on a different period before trusting any threshold.
"""

from datetime import timedelta
from math import sqrt
from statistics import mean, stdev

from bot import Signal

HORIZONS = (15, 30, 60)
BUCKETS = ((0.0, 0.5), (0.5, 0.55), (0.55, 0.6), (0.6, 0.65), (0.65, 0.7), (0.7, 1.01))


def study(bars_by_symbol_day):
    rows = []
    for bars in bars_by_symbol_day:
        mids = {bar.time: (bar.bid + bar.ask) / 2 for bar in bars
                if bar.bid and bar.ask and 0 < bar.bid <= bar.ask}
        for bar in bars:
            window = bar.pressure
            start = mids.get(bar.time)
            if window is None or start is None:
                continue
            classified = window.buy_initiated_dollars + window.sell_initiated_dollars
            if classified <= 0 or window.expected_classified_dollars <= 0:
                continue
            forward = {}
            for minutes in HORIZONS:
                later = mids.get(bar.time + timedelta(minutes=minutes))
                if later is not None:
                    forward[minutes] = (later / start - 1) * 10_000
            rows.append((window.buy_initiated_dollars / classified,
                         classified >= 3 * window.expected_classified_dollars,
                         window.high_mid <= window.low_mid * 1.005, forward, (bar.symbol, bar.time)))
    return summarize(rows)


def _episodes(keys):
    """Separate events: windows of one symbol more than 15 minutes apart (overlapping windows count once)."""
    count, last = 0, {}
    for symbol, time in sorted(keys):
        if symbol not in last or time - last[symbol] > timedelta(minutes=15):
            count += 1
        last[symbol] = time
    return count


def _stats(values, keys=()):
    if not values:
        return {"n": 0}
    result = {"n": len(values), "events": _episodes(keys), "mean_bp": round(mean(values), 2),
              "up_pct": round(100 * sum(v > 0 for v in values) / len(values), 1)}
    if len(values) > 1 and stdev(values) > 0:
        result["t"] = round(mean(values) / (stdev(values) / sqrt(len(values))), 2)
    return result


def summarize(rows):
    groups = {"all_windows": rows,
              "heavy_and_flat": [row for row in rows if row[1] and row[2]]}
    result = {}
    for name, selected in groups.items():
        result[name] = {}
        for low, high in BUCKETS:
            bucket = [row for row in selected if low <= row[0] < high]
            label = f"{int(low * 100)}-{min(100, int(high * 100))}%"
            result[name][label] = {f"{h}m": _stats([row[3][h] for row in bucket if h in row[3]],
                                                       [row[4] for row in bucket if h in row[3]])
                                   for h in HORIZONS}
    return result


class AbsorptionScanner:
    """Trading rule from hypothesis 1 (README, fixed 2026-11-16 before the recent-data test).

    Signal when the last 15 minutes had classified dollars >= 3x the usual for that minute, a mid-quote
    range <= 0.5% and a buy-initiated share of 50-70%. The floor is the window's lowest mid, so PaperBot
    exits once a minute closes below the flat range; the holding limit is set on the bot (60 minutes).
    One signal per symbol per hour, so a single episode is not bought again minute after minute.
    """

    def __init__(self, *, low=0.5, high=0.7, cooldown_minutes=60):
        self.low, self.high = low, high
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.last: dict[str, object] = {}

    def update(self, bars):
        signals = {}
        for bar in bars:
            window = bar.pressure
            if window is None:
                continue
            classified = window.buy_initiated_dollars + window.sell_initiated_dollars
            if classified <= 0 or window.expected_classified_dollars <= 0:
                continue
            share = window.buy_initiated_dollars / classified
            if (classified >= 3 * window.expected_classified_dollars
                    and 0 < window.low_mid <= window.high_mid <= window.low_mid * 1.005
                    and self.low <= share < self.high):
                last = self.last.get(bar.symbol)
                if last is None or bar.time - last >= self.cooldown:
                    self.last[bar.symbol] = bar.time
                    signals[bar.symbol] = Signal(bar.symbol, window.low_mid)
        return signals
