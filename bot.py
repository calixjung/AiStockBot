"""Replay-only US equity scanner and paper trading committee."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import json
from pressure import PressureWindow, stalled_buying


@dataclass(frozen=True)
class Bar:
    time: datetime
    symbol: str
    high: float
    low: float
    close: float
    volume: int
    relative_volume: float | None = None
    bid: float | None = None
    ask: float | None = None
    quote_age_seconds: float = 0
    halted: bool = False
    pressure: PressureWindow | None = None


@dataclass(frozen=True)
class Market:
    spy_below_vwap: bool
    qqq_below_vwap: bool
    declining_fraction: float

    def buy_veto(self):
        if self.spy_below_vwap and self.qqq_below_vwap and self.declining_fraction >= 0.70:
            return "weak_market"
        return None


@dataclass(frozen=True)
class SocialEvidence:
    summary: str = ""
    sources: tuple[str, ...] = ()
    flagged: bool = False


class SocialReviewer:
    """Extension point for X/Reddit and public filing analysis."""

    def review(self, symbol: str, as_of: datetime) -> SocialEvidence | None:
        return None


@dataclass
class Setup:
    high: float
    low: float
    expires: datetime
    above_count: int = 0


@dataclass(frozen=True)
class Signal:
    symbol: str
    floor: float


class Scanner:
    def __init__(self, *, require_pressure=False):
        self.require_pressure = require_pressure
        self.setups: dict[str, Setup] = {}
        self.history: dict[str, list[Bar]] = {}

    def update(self, bar: Bar) -> Signal | None:
        history = self.history.setdefault(bar.symbol, [])
        if history and bar.time - history[-1].time != timedelta(minutes=1):
            history.clear()
            self.setups.pop(bar.symbol, None)
        history.append(bar)
        del history[:-15]

        setup = self.setups.get(bar.symbol)
        if setup and (bar.time > setup.expires or bar.close < setup.low):
            del self.setups[bar.symbol]
            setup = None
        if setup:
            setup.above_count = setup.above_count + 1 if bar.close > setup.high else 0
            if setup.above_count == 2:
                del self.setups[bar.symbol]
                return Signal(bar.symbol, setup.high)
        elif len(history) == 15 and bar.relative_volume is not None:
            high = max(item.high for item in history)
            low = min(item.low for item in history)
            traded_value = sum(item.close * item.volume for item in history)
            if bar.relative_volume >= 3 and (high - low) / low <= 0.005 and traded_value >= 1_000_000:
                if self.require_pressure and (bar.pressure is None or not stalled_buying(bar.pressure)):
                    return None
                self.setups[bar.symbol] = Setup(high, low, bar.time + timedelta(minutes=10))
        return None


@dataclass
class Position:
    quantity: int
    cost: float
    floor: float
    opened: datetime


class PaperBot:
    def __init__(self, name, *, market_filter=False, social=None):
        self.name = name
        self.market_filter = market_filter
        self.social = social
        self.cash = 10_000.0
        self.positions: dict[str, Position] = {}
        self.pending: dict[str, Signal] = {}
        self.exit_pending: dict[str, str] = {}
        self.marks: dict[str, float] = {}
        self.peak = self.cash
        self.max_drawdown = 0.0
        self.session = None
        self.session_start = self.cash
        self.loss_stop = False
        self.fills = 0
        self.trades = []

    def log(self, bar, event, **details):
        print(json.dumps(dict(variant=self.name, time=bar.time.isoformat(),
                              symbol=bar.symbol, event=event, **details), ensure_ascii=False))

    def equity(self):
        return self.cash + sum(
            position.quantity * self.marks[symbol] for symbol, position in self.positions.items()
        )

    @staticmethod
    def quote_ok(bar):
        return (not bar.halted and 0 <= bar.quote_age_seconds <= 5
                and bar.bid is not None and bar.ask is not None
                and 0 < bar.bid <= bar.ask)

    def veto(self, bar, market):
        if not self.quote_ok(bar):
            return "unusable_quote"
        if (bar.ask - bar.bid) / bar.bid > 0.003:
            return "wide_spread"
        if self.loss_stop:
            return "daily_loss_limit"
        if self.market_filter:
            return market.buy_veto()
        return None

    def step(self, bars, market, signals):
        # Replay input contains one regular-session batch per minute in New York time.
        session = bars[0].time.date()
        if self.session != session:
            self.session = session
            self.session_start = self.equity()
            self.loss_stop = False
            self.pending.clear()
        for bar in bars:
            if self.quote_ok(bar):
                self.marks[bar.symbol] = bar.bid
        if self.equity() <= self.session_start * 0.99:
            self.loss_stop = True

        for bar in bars:
            symbol = bar.symbol
            position = self.positions.get(symbol)
            reason = self.exit_pending.get(symbol)
            if position and reason and self.quote_ok(bar):
                price = bar.bid * 0.9999
                proceeds = position.quantity * price - 1.0
                self.cash += proceeds
                del self.positions[symbol]
                del self.exit_pending[symbol]
                self.fills += 1
                self.trades.append((symbol, position.opened, proceeds - position.cost))
                self.log(bar, "paper_sell", reason=reason, quantity=position.quantity,
                         price=round(price, 4), pnl=round(proceeds - position.cost, 2))
                position = None
            if position and not reason and not bar.halted:
                if bar.close < position.floor:
                    self.exit_pending[symbol] = "failed_breakout"
                elif bar.time - position.opened >= timedelta(minutes=30):
                    self.exit_pending[symbol] = "time_limit"

        for bar in bars:
            signal = self.pending.pop(bar.symbol, None)
            if signal is None or bar.symbol in self.positions:
                continue
            reason = self.veto(bar, market)
            if reason or bar.close <= signal.floor:
                self.log(bar, "buy_veto", reason=reason or "breakout_reversed")
                continue
            price = bar.ask * 1.0001
            quantity = int(max(0, min(self.cash, 1_000) - 1.0) / price)
            if not quantity:
                self.log(bar, "buy_veto", reason="insufficient_budget")
                continue
            cost = quantity * price + 1.0
            self.cash -= cost
            self.positions[bar.symbol] = Position(quantity, cost, signal.floor, bar.time)
            self.fills += 1
            self.log(bar, "paper_buy", quantity=quantity, price=round(price, 4))

        self.pending.clear()
        for bar in bars:
            signal = signals.get(bar.symbol)
            if signal is None or bar.symbol in self.positions:
                continue
            reason = self.veto(bar, market)
            if self.social is not None:
                evidence = self.social.review(bar.symbol, bar.time)
                if evidence is None:
                    self.log(bar, "social_unavailable")
                else:
                    self.log(bar, "social_review", summary=evidence.summary, sources=evidence.sources)
                    if evidence.flagged:
                        reason = reason or "social_risk"
            if reason:
                self.log(bar, "buy_veto", reason=reason)
            else:
                self.pending[bar.symbol] = signal
                self.log(bar, "buy_queued")

        equity = self.equity()
        self.peak = max(self.peak, equity)
        self.max_drawdown = max(self.max_drawdown, (self.peak - equity) / self.peak)

    def report(self):
        return dict(variant=self.name, equity=round(self.equity(), 2),
                    net_pnl=round(self.equity() - 10_000, 2),
                    max_drawdown_pct=round(self.max_drawdown * 100, 3),
                    fills=self.fills, open_positions=sorted(self.positions))




