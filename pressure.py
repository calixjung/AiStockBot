"""Measure buy-side pressure from time-aligned trade and quote aggregates."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PressureWindow:
    buy_initiated_dollars: float
    sell_initiated_dollars: float
    expected_classified_dollars: float
    high_mid: float
    low_mid: float


def stalled_buying(window: PressureWindow) -> bool:
    """Flag an observation for review, using provisional replay thresholds."""
    buy = window.buy_initiated_dollars
    sell = window.sell_initiated_dollars
    total = buy + sell
    return (
        buy >= 0 and sell >= 0 and total > 0
        and window.expected_classified_dollars > 0
        and total >= 3 * window.expected_classified_dollars
        and buy / total >= 0.70
        and 0 < window.low_mid <= window.high_mid <= window.low_mid * 1.005
    )


if __name__ == "__main__":
    cases = {
        "buying_stalled": PressureWindow(800_000, 200_000, 300_000, 50.15, 50),
        "price_already_moved": PressureWindow(800_000, 200_000, 300_000, 51, 50),
        "sell_pressure": PressureWindow(200_000, 800_000, 300_000, 50.15, 50),
    }
    for name, window in cases.items():
        print(f"{name}: {stalled_buying(window)}")
