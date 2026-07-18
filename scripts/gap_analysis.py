"""Offline close->next-open gap study for the MOC/OPG execution question.

Research context (research_notes/2026-07-18-alpaca-moc-opg-orders.md): the
daily strategy decides AFTER the close and its market order fills near the
NEXT OPEN, while the backtest (cheat-on-close) assumes a fill AT the decision
bar's close. The difference per execution is the overnight gap. This script
measures that gap from cached daily bars:

  1. unconditionally (every day), and
  2. conditionally on the strategy's OWN entry/exit execution days -
     momentum entries follow strong closes, so continuation could make the
     open systematically worse for us on BOTH sides. That is the question.

Direction-adjusted cost convention (adverse positive, like TCA):
  buy at open  : cost_bps = +gap  (open above yesterday's close = pay more)
  sell at open : cost_bps = -gap  (open below yesterday's close = receive less)

Pure read-only research: loads cached bars, prints stats, changes nothing.

Run:  .venv/Scripts/python.exe scripts/gap_analysis.py [SYMBOL] [LOOKBACK]
      (defaults: SPY 100; PYTHONPATH handled when run from the repo root)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def _stats(x: pd.Series) -> str:
    if len(x) == 0:
        return "n=0"
    return (f"n={len(x):>4}  mean={x.mean():+7.1f}  median={x.median():+7.1f}  "
            f"std={x.std():6.1f}  p5={x.quantile(0.05):+7.1f}  p95={x.quantile(0.95):+7.1f}")


def main(symbol: str = "SPY", lookback: int = 100) -> None:
    from quant.data.loaders import fetch_bars
    from quant.strategies.registry import get_strategy_cls

    data = fetch_bars(symbol, "2020-01-01", "1d")
    close, open_ = data["close"].astype(float), data["open"].astype(float)

    # gap on execution day t: open[t] vs PREVIOUS close [t-1]
    gap_bps = (open_ / close.shift(1) - 1.0) * 1e4
    gap_bps = gap_bps.dropna()

    strat = get_strategy_cls("momentum")(lookback=lookback)
    sig = strat.generate_signals(data)
    # signal at close of day t -> execution at open of day t+1
    entry_exec = sig["entries"].shift(1).fillna(False).astype(bool)
    exit_exec = sig["exits"].shift(1).fillna(False).astype(bool)

    buy_cost = gap_bps[entry_exec.reindex(gap_bps.index, fill_value=False)]
    sell_cost = -gap_bps[exit_exec.reindex(gap_bps.index, fill_value=False)]

    years = (len(data) - 1) / 252.0
    n_exec = len(buy_cost) + len(sell_cost)
    total_cost = buy_cost.sum() + sell_cost.sum()

    print(f"\n=== close -> next-open gap study: {symbol}  "
          f"({data.index[0].date()} -> {data.index[-1].date()}, {len(data)} bars, "
          f"momentum lookback={lookback}) ===")
    print("all bps figures are per execution, adverse = positive\n")
    print(f"unconditional gap (bps)      : {_stats(gap_bps)}")
    print(f"unconditional |gap| mean     : {gap_bps.abs().mean():6.1f} bps")
    print(f"BUY cost on entry days       : {_stats(buy_cost)}")
    print(f"SELL cost on exit days       : {_stats(sell_cost)}")
    if len(buy_cost) and len(sell_cost):
        rt = buy_cost.mean() + sell_cost.mean()
        print(f"\nround-trip drift cost        : {rt:+7.1f} bps "
              f"(buy {buy_cost.mean():+.1f} + sell {sell_cost.mean():+.1f})")
        print(f"executions per year          : {n_exec / years:5.1f}")
        print(f"annualized drift cost        : {total_cost / years:+7.1f} bps/yr "
              f"(vs measured spread cost ~0-2 bps/side)")
        print(f"per-execution noise (std)    : {pd.concat([buy_cost, sell_cost]).std():6.1f} bps "
              "-> tracking error vs the backtest, even if the mean is small")
    print("\nroutes: MOC (cls, decide ~15:45) removes this gap entirely but "
          "trades on a ~99%-complete bar;\n        OPG (opg, decide after close) "
          "keeps the gap but makes it the DEFINED fill (align backtest to next-open).")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(symbol=a[0] if a else "SPY", lookback=int(a[1]) if len(a) > 1 else 100)
