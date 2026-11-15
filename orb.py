"""Long-only opening range breakout (ORB) on the day's highest relative-volume symbols."""

from datetime import timedelta
from statistics import median

from bot import Signal


class OpeningRangeScanner:
    """Comparison candidate, not the main strategy.

    - Opening range: the first `minutes` regular-session minute bars (09:30 to 09:35 by default).
    - Relative volume: opening-range volume divided by the median of the same symbol's earlier sessions.
    - Selection at the end of the range: up candle (last close above the first trade) and relative
      volume >= `min_relative`, keep the `top` highest. The first session of each symbol only builds history.
    - Signal: the first later minute that closes above the range high; the range high is the floor,
      so PaperBot applies the same entry check and exits as the breakout strategies.
    """

    def __init__(self, openings, *, minutes=5, top=3, min_relative=1.0, stop="high", min_width=0.0):
        # stop="high": exit floor is the range high (same exits as the breakout strategies);
        # stop="low": floor is the range low, as in published ORB studies that hold until the close.
        self.stop = stop
        # Skip days whose opening range is narrower than this fraction: with 0.25% commission each side a
        # trade must move more than ~0.55% to pay its costs, and quiet openings rarely lead to such moves.
        self.min_width = min_width
        self.openings = openings
        self.minutes = minutes
        self.top = top
        self.min_relative = min_relative
        self.history: dict[str, list[int]] = {}
        self.session = None
        self.ranges: dict[str, list] = {}
        self.selected: dict[str, float] | None = None
        self.fired: set[str] = set()
        self.selections = []

    def select(self):
        candidates = []
        for symbol, (high, low, volume, close) in self.ranges.items():
            earlier = self.history.get(symbol)
            opening = self.openings.get((self.session, symbol))
            if earlier and opening is not None and close > opening and (high - low) / low >= self.min_width:
                typical = median(earlier)
                if typical and volume / typical >= self.min_relative:
                    candidates.append((volume / typical, symbol, (high, low)))
        candidates.sort(reverse=True)
        self.selected = {symbol: levels for _, symbol, levels in candidates[:self.top]}
        self.selections.append({"date": self.session.isoformat(),
                                "symbols": [[symbol, round(ratio, 2)] for ratio, symbol, _ in candidates[:self.top]]})
        # Today's volume joins the reference only after today's selection.
        for symbol, (_, _, volume, _) in self.ranges.items():
            self.history.setdefault(symbol, []).append(volume)

    def update(self, bars):
        signals = {}
        timestamp = bars[0].time
        if timestamp.date() != self.session:
            self.session = timestamp.date()
            self.ranges = {}
            self.selected = None
            self.fired = set()
        range_end = timestamp.replace(hour=9, minute=30, second=0, microsecond=0) + timedelta(minutes=self.minutes)
        if timestamp <= range_end:
            for bar in bars:
                high, low, volume, _ = self.ranges.get(bar.symbol, (bar.high, bar.low, 0, None))
                self.ranges[bar.symbol] = [max(high, bar.high), min(low, bar.low), volume + bar.volume, bar.close]
            return signals
        if self.selected is None:
            self.select()
        for bar in bars:
            levels = self.selected.get(bar.symbol)
            if levels is not None and bar.symbol not in self.fired and bar.close > levels[0]:
                self.fired.add(bar.symbol)
                signals[bar.symbol] = Signal(bar.symbol, levels[0] if self.stop == "high" else levels[1])
        return signals
