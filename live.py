"""Paper trading driven by a stream of market events (replayed files today, a live feed later).

The stream is (symbol, seconds after New York midnight, bid, ask, trade or None) in time order. Bars are built
with `historical.MinuteBars`, so a replay through this module must produce the same paper trades as
`historical.run`; `python live.py --check FOLDER` verifies exactly that. No real orders exist anywhere here.

Live data needs a market-data account (for example Alpaca). That is a user decision: free feeds such as IEX
carry only a few percent of US volume, so relative volume and buy/sell shares would not match these tests;
the full consolidated feed is paid. `LiveFeed` documents what an adapter must provide.
"""

import argparse
from collections import defaultdict
import csv
from datetime import date, timedelta
import heapq
import json
from pathlib import Path
from statistics import mean

from absorption import AbsorptionScanner
from bot import Market, PaperBot, Scanner
from historical import MinuteBars
from orb import OpeningRangeScanner

INDEX = ("SPY", "QQQ")
# (variant, signal source, market veto, maximum hold minutes)
STRATEGIES = [("breakout", "breakout", False, 30), ("breakout_market_veto", "breakout", True, 30),
              ("orb", "orb", False, 30),
              ("orb_market_veto", "orb", True, 30), ("absorption", "absorption", False, 60),
              ("absorption_market_veto", "absorption", True, 60),
              ("orb_hold_to_close", "orb_to_close", False, 400)]


class AutoStop:
    """Stop new buys for a strategy whose live trades are clearly worse than expected.

    After `min_trades` closed trades, if the average trade (costs included) is below `floor_bp`, the
    strategy is suspended. Exits still run. The default floor is the 2019 random-entry 5th percentile.
    """

    def __init__(self, min_trades=20, floor_bp=-19.3):
        self.min_trades = min_trades
        self.floor_bp = floor_bp

    def review(self, bot):
        returns = [ratio * 10_000 for *_, ratio in bot.trades]
        if not bot.suspended and len(returns) >= self.min_trades and mean(returns) < self.floor_bp:
            bot.suspended = True
            return {"variant": bot.name, "event": "auto_stop", "trades": len(returns),
                    "mean_bp": round(mean(returns), 1), "floor_bp": self.floor_bp}
        return None


class LiveSession:
    def __init__(self, symbols, offset, *, strategies=STRATEGIES, auto_stop=None, quiet=False):
        self.symbols = [symbol for symbol in symbols if symbol not in INDEX]
        self.offset = offset
        self.references = defaultdict(lambda: defaultdict(list))
        self.openings = {}
        self.strategies = strategies
        self.bots = [PaperBot(name, market_filter=veto, max_hold_minutes=hold, quiet=quiet)
                     for name, _, veto, hold in strategies]
        self.scanners = {"breakout": Scanner(), "orb": OpeningRangeScanner(self.openings),
                         "absorption": AbsorptionScanner(),
                         "orb_to_close": OpeningRangeScanner(self.openings, stop="low")}
        self.auto_stop = auto_stop
        self.notices = []
        self.day = None

    # ---- day lifecycle -------------------------------------------------------------------------
    def start_day(self, day, trading=True):
        self.day = day
        self.trading = trading
        self.builders = {symbol: MinuteBars(symbol, day, self.offset, self.references[symbol])
                         for symbol in self.symbols}
        self.index = {symbol: {"value": 0.0, "volume": 0.0, "last": None, "minute": None, "state": None}
                      for symbol in INDEX}
        self.index_states = {}
        self.latest_index = {}
        self.closes = {}
        self.pending = defaultdict(list)

    def end_day(self):
        for builder in self.builders.values():
            for bar in builder.finish():
                self.pending[bar.time].append(bar)
        self._close_index_minutes(None)
        self._process(None)
        for symbol, builder in self.builders.items():
            self.openings[self.day, symbol] = builder.opening
            for minute, window in builder.daily_windows.items():
                self.references[symbol][minute].append(window)

    # ---- events --------------------------------------------------------------------------------
    def on_event(self, symbol, seconds, bid, ask, trade=None):
        if symbol in self.index:
            self._index_event(symbol, seconds, trade)
            return
        builder = self.builders.get(symbol)
        if builder is None:
            return
        if trade is not None and builder.opening is None and 570 <= seconds // 60 < 960:
            self.openings[self.day, symbol] = trade[0]
        for bar in builder.event(seconds, bid, ask, trade):
            self.pending[bar.time].append(bar)

    def on_clock(self, seconds):
        """Call when the clock has reached `seconds`; bars of earlier minutes are then complete."""
        for builder in self.builders.values():
            for bar in builder.flush(seconds):
                self.pending[bar.time].append(bar)
        self._close_index_minutes(int(seconds // 60))
        cutoff = next(iter(self.builders.values())).base + timedelta(minutes=int(seconds // 60))
        self._process(cutoff)

    # ---- internals -----------------------------------------------------------------------------
    def _index_event(self, symbol, seconds, trade):
        state = self.index[symbol]
        event_minute = int(seconds // 60)
        if not 570 <= event_minute < 960:
            return
        if state["minute"] is not None and event_minute != state["minute"] and state["last"] is not None:
            self._record_index(symbol, state["minute"])
        state["minute"] = event_minute
        if trade is not None:
            price, size, _ = trade
            state["value"] += price * size
            state["volume"] += size
            state["last"] = price

    def _record_index(self, symbol, minute):
        state = self.index[symbol]
        stamp = next(iter(self.builders.values())).base + timedelta(minutes=minute + 1)
        state["state"] = state["last"] < state["value"] / state["volume"]
        self.index_states.setdefault(stamp, {})[symbol] = state["state"]

    def _close_index_minutes(self, now_minute):
        for symbol, state in self.index.items():
            if state["minute"] is not None and state["last"] is not None and (now_minute is None or now_minute > state["minute"]):
                self._record_index(symbol, state["minute"])
                state["minute"] = None if now_minute is None else now_minute

    def _market(self, stamp):
        # Mirrors historical.market_states: each index keeps its latest state; a minute in which
        # neither index printed, or before both have printed, is not called weak.
        entry = self.index_states.get(stamp)
        known = (False, False)
        if entry:
            self.latest_index.update(entry)
            if all(symbol in self.latest_index for symbol in INDEX):
                known = (self.latest_index["SPY"], self.latest_index["QQQ"])
        traded = [close < self.openings[self.day, symbol] for symbol, close in self.closes.items()]
        return Market(known[0], known[1], sum(traded) / len(traded))

    def _process(self, cutoff):
        for stamp in sorted(stamp for stamp in self.pending if cutoff is None or stamp <= cutoff):
            bars = self.pending.pop(stamp)
            for bar in bars:
                self.closes[bar.symbol] = bar.close
            market = self._market(stamp)
            signals = {"breakout": {}, "orb": self.scanners["orb"].update(bars),
                       "absorption": self.scanners["absorption"].update(bars),
                       "orb_to_close": self.scanners["orb_to_close"].update(bars)}
            for bar in bars:
                signal = self.scanners["breakout"].update(bar)
                if signal:
                    signals["breakout"][bar.symbol] = signal
            if not self.trading:
                continue
            for (_, source, _, _), bot in zip(self.strategies, self.bots):
                bot.step(bars, market, signals[source])
                if self.auto_stop:
                    notice = self.auto_stop.review(bot)
                    if notice:
                        self.notices.append(notice)
                        print(json.dumps(notice))


class ReplayFeed:
    """Events from extracted folders (YYYY-MM-DD/SYMBOL/message.csv + orderbook.csv), merged by time."""

    def __init__(self, sessions, index=None):
        self.sessions = Path(sessions)
        self.index = Path(index) if index else self.sessions.parent / "index"

    def days(self):
        return sorted(date.fromisoformat(p.name) for p in self.sessions.iterdir() if p.is_dir())

    def symbols(self, day):
        folders = [p for p in (self.sessions / day.isoformat()).iterdir() if p.is_dir()]
        folders += [p for p in (self.index / day.isoformat()).iterdir() if p.is_dir()] if (self.index / day.isoformat()).is_dir() else []
        return {p.name: p for p in folders}

    @staticmethod
    def _events(symbol, folder, order):
        with (folder / "message.csv").open(newline="") as messages, (folder / "orderbook.csv").open(newline="") as quotes:
            for row, (message, quote) in enumerate(zip(csv.reader(messages), csv.reader(quotes), strict=True)):
                trade = None
                if message[1] in {"4", "5"} and message[5] in {"-1", "0", "1"}:
                    trade = (int(message[4]) / 10_000, int(message[3]), int(message[5]))
                yield float(message[0]), order, row, symbol, int(quote[2]) / 10_000, int(quote[0]) / 10_000, trade

    def events(self, day):
        folders = self.symbols(day)
        streams = [self._events(symbol, folder, order) for order, (symbol, folder) in enumerate(sorted(folders.items()))]
        for seconds, _, _, symbol, bid, ask, trade in heapq.merge(*streams):
            yield symbol, seconds, bid, ask, trade


class LiveFeed:
    """What a real-time adapter must yield, in time order, during 09:30-16:00 New York time:
    (symbol, seconds after local midnight, best bid, best ask, (price, size, side) or None),
    side -1 buyer-initiated, 1 seller-initiated, 0 unknown, for the trading symbols and SPY, QQQ.
    Not implemented: it needs a market-data account and API key chosen by the user.
    """

    def events(self, day):
        raise NotImplementedError("A real-time data account is required; see the module docstring.")


def replay(sessions, offset, *, index=None, quiet=True, auto_stop=None):
    feed = ReplayFeed(sessions, index)
    days = feed.days()
    symbols = sorted({symbol for day in days for symbol in feed.symbols(day)})
    session = LiveSession(symbols, offset, auto_stop=auto_stop, quiet=quiet)
    for position, day in enumerate(days):
        session.start_day(day, trading=position > 0)
        clock = None
        for symbol, seconds, bid, ask, trade in feed.events(day):
            minute = int(seconds // 60)
            if clock is not None and minute > clock:
                session.on_clock(seconds)
            clock = minute
            session.on_event(symbol, seconds, bid, ask, trade)
        session.end_day()
    return session


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sessions", type=Path, help="Extracted YYYY-MM-DD/SYMBOL folders to replay")
    parser.add_argument("--utc-offset", type=int, choices=(-5, -4), default=-5)
    parser.add_argument("--check", action="store_true",
                        help="Compare paper trades with historical.run on the same data")
    args = parser.parse_args()
    session = replay(args.sessions, args.utc_offset, auto_stop=None if args.check else AutoStop())
    live_trades = {bot.name: [(s, o.isoformat(), round(p, 6)) for s, o, p, _ in bot.trades] for bot in session.bots}
    if args.check:
        import contextlib
        import io
        from historical import run
        with contextlib.redirect_stdout(io.StringIO()):
            # historical.run keeps every bot's trades; rebuild them through its PaperBots.
            import historical
            captured = {}
            original = historical.PaperBot.__init__

            def remember(self, name, **kwargs):
                original(self, name, **kwargs)
                captured[name] = self
            historical.PaperBot.__init__ = remember
            run(args.sessions, offset=args.utc_offset, random_runs=0)
            historical.PaperBot.__init__ = original
        mismatches = 0
        for name, trades in live_trades.items():
            expected = [(s, o.isoformat(), round(p, 6)) for s, o, p, _ in captured[name].trades]
            same = trades == expected
            mismatches += not same
            print(f"{name}: live {len(trades)} trades, historical {len(expected)} trades, {'same' if same else 'DIFFERENT'}")
        raise SystemExit(1 if mismatches else 0)
    print(json.dumps({bot.name: bot.report() for bot in session.bots}, indent=1))
