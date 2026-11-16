"""Monthly momentum paper portfolio (no real orders). Rules are fixed in README, 2026-11-17.

Each run downloads the latest public daily prices, replays the fixed rule from the start date and rewrites
paper/momentum.md and paper/momentum.json. Commit the two files after every run.
"""

import argparse
from datetime import date
import json
from pathlib import Path
import shutil
import urllib.request

import numpy as np

from swing_stocks import BUY_COST, START_CASH, equal_weight, indicators, load, simulate, universes

START = "2026-11-02"  # signal day of the first rebalance; fills on the next open
BASE = "https://huggingface.co/datasets/defeatbeta/yahoo-finance-data/resolve/main/data/US/"
FILES = ("stock_prices.parquet", "stock_profile.parquet")


def download(folder):
    folder.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        partial = folder / (name + ".part")
        with urllib.request.urlopen(BASE + name, timeout=120) as response, open(partial, "wb") as handle:
            shutil.copyfileobj(response, handle, 1 << 20)
        partial.replace(folder / name)


def run(folder):
    frame, calendar, spy = load(folder)
    universe = universes(frame, calendar)
    names = sorted({symbol for members in universe.values() for symbol in members})
    wide, _, momentum, _ = indicators(frame, calendar, names)
    detail = {}
    stats = simulate(wide, calendar, spy, universe, START, "2100-01-01", None, None, None,
                     momentum=momentum, detail=detail)
    days = np.where(calendar >= np.datetime64(START))[0]
    first_fill = days[1]
    spy_shares = START_CASH / (spy["open"].to_numpy()[first_fill] * BUY_COST)
    spy_value = float(spy_shares * spy["close"].to_numpy()[days[-1]])
    basket = equal_weight(wide, calendar, universe, START, "2100-01-01")
    equity = detail["curve"][-1]
    peak = np.maximum.accumulate(np.array(detail["curve"]))
    return {
        "as_of": detail["dates"][-1], "start_signal_day": START, "run_on": date.today().isoformat(),
        "equity": round(equity, 2), "return_pct": round((equity / START_CASH - 1) * 100, 2),
        "max_drawdown_pct": round(float(np.max(1 - np.array(detail["curve"]) / peak)) * 100, 1),
        "cash": round(detail["cash"], 2),
        "spy_equity": round(spy_value, 2), "spy_return_pct": round((spy_value / START_CASH - 1) * 100, 2),
        "universe_equal_weight_return_pct": round((basket["final"] / START_CASH - 1) * 100, 2),
        "positions": [{**p, "cost": round(p["cost"], 2), "value": round(p["value"], 2),
                       "return_pct": round((p["value"] / p["cost"] - 1) * 100, 2)} for p in detail["positions"]],
        "closed_trades": detail.get("closed", []),
        "orders_for_next_open": {"sell": detail["pending_sell"], "buy": detail["pending_buy"]},
        "closed_trade_stats": {k: stats.get(k) for k in ("trades", "win_rate_pct", "avg_trade_pct", "avg_vs_spy_pct")},
        "equity_curve": dict(zip(detail["dates"], [round(v, 2) for v in detail["curve"]])),
    }


def markdown(result):
    lines = [
        "# 월간 모멘텀 모의 운용 (실제 주문 없음)", "",
        f"기준일 {result['as_of']} 종가 · 실행일 {result['run_on']} · 시작 신호일 {result['start_signal_day']} · 가상 계좌 $10,000 · 수수료 한쪽 0.25%", "",
        "| | 평가액 | 수익률 |", "|---|---|---|",
        f"| 모멘텀 모의 계좌 | ${result['equity']:,.2f} | {result['return_pct']:+.2f}% |",
        f"| SPY 같은 날 매수 후 보유 | ${result['spy_equity']:,.2f} | {result['spy_return_pct']:+.2f}% |",
        f"| 100종목 같은 비중(청산 비용 포함) | — | {result['universe_equal_weight_return_pct']:+.2f}% |", "",
        f"모의 계좌 최대 낙폭 {result['max_drawdown_pct']}% · 현금 ${result['cash']:,.2f}", "",
        "## 현재 보유", "", "| 종목 | 매수일 | 매수 금액 | 현재 평가 | 수익률 |", "|---|---|---|---|---|",
    ]
    for p in sorted(result["positions"], key=lambda item: -item["return_pct"]):
        lines.append(f"| {p['symbol']} | {p['bought']} | ${p['cost']:,.2f} | ${p['value']:,.2f} | {p['return_pct']:+.2f}% |")
    orders = result["orders_for_next_open"]
    lines += ["", "## 다음 거래일 시가 주문(모의)", "",
              f"매도: {', '.join(orders['sell']) or '없음'} · 매수: {', '.join(orders['buy']) or '없음'}", "",
              "## 끝난 거래", ""]
    if result["closed_trades"]:
        lines += ["| 종목 | 매수일 | 매도일 | 수익률(비용 포함) |", "|---|---|---|---|"]
        lines += [f"| {t['symbol']} | {t['bought']} | {t['sold']} | {t['return_pct']:+.2f}% |" for t in result["closed_trades"]]
    else:
        lines.append("아직 없음")
    lines += ["", "규칙과 판정 시점(2027-11)은 README의 \"월간 모멘텀 모의 운용\"에 있습니다. "
              "과거 시험에서는 한 해 -30% 안팎의 하락이 여러 번 있었습니다.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("D:/us-stock-data/daily-hf"))
    parser.add_argument("--no-download", action="store_true", help="Use the files already in --data")
    args = parser.parse_args()
    if not args.no_download:
        download(args.data)
    result = run(args.data)
    out = Path(__file__).resolve().parent / "paper"
    out.mkdir(exist_ok=True)
    (out / "momentum.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    (out / "momentum.md").write_text(markdown(result), encoding="utf-8")
    print(f"{result['as_of']}: paper {result['return_pct']:+.2f}%, SPY {result['spy_return_pct']:+.2f}%, "
          f"{len(result['positions'])} positions")
