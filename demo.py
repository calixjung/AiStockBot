"""Run a deterministic replay with synthetic bars and quotes."""

from datetime import datetime, timedelta, timezone
import json

from pressure import PressureWindow, stalled_buying

from bot import Bar, Market, PaperBot, Scanner, SocialEvidence, SocialReviewer


class DemoSocialReviewer(SocialReviewer):
    def review(self, symbol, as_of):
        if symbol == "SIM_RUMOR":
            return SocialEvidence("Synthetic fixture: copied rumor without primary confirmation.", flagged=True)
        return None


def replay():
    scanner = Scanner()
    pressure_scanner = Scanner(require_pressure=True)
    bots = [
        PaperBot("breakout"),
        PaperBot("market_veto", market_filter=True),
        PaperBot("committee", market_filter=True, social=DemoSocialReviewer()),
        PaperBot("pressure_committee", market_filter=True, social=DemoSocialReviewer()),
    ]
    symbols = ("SIM_CLEAN", "SIM_WEAK", "SIM_RUMOR", "SIM_WIDE", "SIM_BALANCED")
    for day in (3, 4, 5, 6, 9):
        start = datetime(2026, 11, day, 9, 30, tzinfo=timezone(-timedelta(hours=5)))
        for minute in range(22):
            close = {15: 50.3, 16: 50.4, 17: 50.5, 18: 50.1, 19: 49.9}.get(minute, 50)
            market = Market(day == 4 or minute >= 18, day == 4 or minute >= 18,
                            0.78 if day == 4 or minute >= 18 else 0.35)
            half_spread = 0.20 if day == 6 and minute == 17 else 0.01
            pressure = None
            if minute == 14:
                pressure = PressureWindow(800_000, 200_000, 300_000, 50.10, 49.90)
                if day == 9:
                    pressure = PressureWindow(500_000, 500_000, 300_000, 50.10, 49.90)
            bars = [Bar(start + timedelta(minutes=minute), symbols[4 if day == 9 else day - 3],
                        close + 0.10, close - 0.10, close, 3_000,
                        3.5 if minute == 14 else None, close - half_spread, close + half_spread, pressure=pressure)]
            signals = {}
            for bar in bars:
                signal = scanner.update(bar)
                if signal:
                    signals[bar.symbol] = signal
            pressure_signals = {}
            for bar in bars:
                signal = pressure_scanner.update(bar)
                if signal:
                    pressure_signals[bar.symbol] = signal
                if bar.pressure is not None:
                    pressure_ok = stalled_buying(bar.pressure)
                    bots[-1].log(bar, "pressure_check", passed=pressure_ok,
                                 buy_share=round(bar.pressure.buy_initiated_dollars /
                                                 (bar.pressure.buy_initiated_dollars + bar.pressure.sell_initiated_dollars) * 100, 1))
            for bot in bots[:-1]:
                bot.step(bars, market, signals)
            bots[-1].step(bars, market, pressure_signals)
    return [bot.report() for bot in bots]


if __name__ == "__main__":
    print(json.dumps({"summary": replay()}, indent=2))

