"""Judge whether a strategy's trades beat chance: per-trade statistics, period split, random-entry baseline."""

import random
from math import sqrt
from statistics import mean, median, stdev

from bot import PaperBot, Signal


class RandomScanner:
    """Baseline: enter a random symbol at a random minute, then the same PaperBot entry check and exits.

    The floor (exit level) is `gap` below the signal bar's close, roughly where a breakout floor sits
    (a 15-minute range of at most 0.5%). A strategy is only interesting if its average trade beats
    most of these random runs under identical costs, budget and exit rules.
    """

    def __init__(self, seed, *, rate=0.0005, gap=0.002):
        self.random = random.Random(seed)
        self.rate = rate
        self.gap = gap

    def update(self, bars):
        return {bar.symbol: Signal(bar.symbol, bar.close * (1 - self.gap))
                for bar in bars if self.random.random() < self.rate}


def random_bots(count):
    return [PaperBot(f"random_{seed}", quiet=True) for seed in range(count)], \
           [RandomScanner(seed) for seed in range(count)]


def trade_stats(trades, split=None):
    """Return statistics in basis points (1 bp = 0.01%) of the amount spent, costs included."""
    def summary(selected):
        returns = [ratio * 10_000 for *_, ratio in selected]
        if not returns:
            return {"trades": 0}
        result = {"trades": len(returns), "win_rate_pct": round(100 * sum(r > 0 for r in returns) / len(returns), 1),
                  "mean_bp": round(mean(returns), 1), "median_bp": round(median(returns), 1),
                  "best_bp": round(max(returns), 1), "worst_bp": round(min(returns), 1),
                  "pnl": round(sum(pnl for _, _, pnl, _ in selected), 2)}
        if len(returns) > 1 and stdev(returns) > 0:
            # t near 0 means the average could easily be luck; about 2 or more is the usual first bar.
            result["t_stat"] = round(mean(returns) / (stdev(returns) / sqrt(len(returns))), 2)
        return result

    result = summary(trades)
    if split is not None:
        result["early"] = summary([trade for trade in trades if trade[1].date() < split])
        result["late"] = summary([trade for trade in trades if trade[1].date() >= split])
    return result


def baseline(bots):
    """Average trade (bp) of each random run, and the spread across runs."""
    means = sorted(mean(ratio * 10_000 for *_, ratio in bot.trades) for bot in bots if bot.trades)
    if not means:
        return {"runs": 0}
    pick = lambda fraction: round(means[min(len(means) - 1, int(fraction * len(means)))], 1)
    return {"runs": len(means), "trades_per_run": round(mean(len(bot.trades) for bot in bots), 1),
            "mean_bp_p05": pick(0.05), "mean_bp_p50": pick(0.5), "mean_bp_p95": pick(0.95), "means": [round(m, 1) for m in means],
            # The most extreme single trades, to spot data errors or overnight holds driving a run.
            "extreme_trades": sorted(([bot.name, symbol, opened.isoformat(), round(ratio * 10_000, 1)]
                                      for bot in bots for symbol, opened, _, ratio in bot.trades),
                                     key=lambda item: abs(item[3]), reverse=True)[:5]}


def beats_random(strategy_mean, random_means):
    """Share of random runs whose average trade was at least as good as the strategy's (lower is better)."""
    if not random_means or strategy_mean is None:
        return None
    return round(sum(m >= strategy_mean for m in random_means) / len(random_means), 2)
