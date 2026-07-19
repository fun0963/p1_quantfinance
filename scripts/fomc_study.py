"""FOMC event-day study: does the scheduler's event blindness actually matter?

Research context (research_notes/2026-07-18-gap-event-calendar.md): the live
scheduler trades through FOMC days without knowing they exist. Before building
ANY event infrastructure (blackout rule, banner), measure with daily bars:

  1. market character: SPY return/volatility on scheduled FOMC decision days
     (and the day before / after) vs all other days;
  2. exposure: how often the momentum strategy actually HELD through FOMC days,
     and what those days contributed;
  3. rule relevance: how often our ~5 executions/year would have coincided with
     an FOMC day at all (a blackout that never binds is dead code);
  4. counterfactual: for entries that DID execute on an FOMC day, what a
     one-day delay (blackout) would have cost or saved.

Dates: configs/fomc_dates.json - SCHEDULED decision days only (a pre-committed
rule cannot know emergency meetings). Read-only research; changes nothing.

Run:  .venv/Scripts/python.exe scripts/fomc_study.py [SYMBOL] [LOOKBACK]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))
sys.path.insert(0, str(_ROOT))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


def _stats(x: pd.Series) -> str:
    if len(x) == 0:
        return "n=0"
    return (f"n={len(x):>4}  mean={x.mean():+7.1f}  median={x.median():+7.1f}  "
            f"std={x.std():6.1f}  mean|r|={x.abs().mean():6.1f}")


def main(symbol: str = "SPY", lookback: int = 100) -> None:
    from quant.data.loaders import fetch_bars
    from quant.strategies.registry import get_strategy_cls

    dates = json.loads((_ROOT / "configs" / "fomc_dates.json").read_text())["dates"]
    fomc = pd.DatetimeIndex(pd.to_datetime(dates))

    data = fetch_bars(symbol, "2020-01-01", "1d")
    days = data.index.tz_localize(None).normalize()
    ret_bps = data["close"].astype(float).pct_change() * 1e4
    range_bps = ((data["high"] - data["low"]) / data["close"]).astype(float) * 1e4

    in_window = fomc[(fomc >= days[0]) & (fomc <= days[-1])]
    pos_idx = days.get_indexer(in_window)
    missing = in_window[pos_idx == -1]
    if len(missing):
        print(f"WARNING: {len(missing)} FOMC dates not in bars (holiday/data gap?): "
              f"{[d.date().isoformat() for d in missing]}")
    hit = pos_idx[pos_idx >= 0]

    is_fomc = np.zeros(len(days), dtype=bool)
    is_fomc[hit] = True
    is_pre = np.zeros(len(days), dtype=bool)
    is_pre[hit[hit - 1 >= 0] - 1] = True
    is_post = np.zeros(len(days), dtype=bool)
    is_post[hit[hit + 1 < len(days)] + 1] = True
    is_other = ~(is_fomc | is_pre | is_post)

    print(f"\n=== FOMC event-day study: {symbol} "
          f"({days[0].date()} -> {days[-1].date()}, {len(days)} bars, "
          f"{is_fomc.sum()} scheduled FOMC days in window) ===")
    print("close-to-close returns, bps\n")
    print(f"FOMC decision day            : {_stats(ret_bps[is_fomc])}")
    print(f"day BEFORE (pre-FOMC drift?) : {_stats(ret_bps[is_pre])}")
    print(f"day AFTER                    : {_stats(ret_bps[is_post])}")
    print(f"all other days               : {_stats(ret_bps[is_other])}")
    print(f"\nintraday range (high-low)/close, mean bps: "
          f"FOMC {range_bps[is_fomc].mean():.0f}  vs  other {range_bps[is_other].mean():.0f}")

    # --- what the momentum strategy actually experienced ---------------------
    strat = get_strategy_cls("momentum")(lookback=lookback)
    sig = strat.generate_signals(data)
    pos = pd.Series(np.nan, index=data.index)
    pos[sig["entries"]] = 1.0
    pos[sig["exits"]] = 0.0
    pos = pos.ffill().fillna(0.0)
    held = pos.shift(1).fillna(0.0).astype(bool).to_numpy()   # exposed DURING day t

    years = (len(days) - 1) / 252.0
    long_fomc = ret_bps[is_fomc & held]
    print(f"\nmomentum(lookback={lookback}) exposure:")
    print(f"  held through {int((is_fomc & held).sum())}/{int(is_fomc.sum())} FOMC days "
          f"(long {held.mean():.0%} of all days)")
    print(f"  FOMC-day P&L while long      : {_stats(long_fomc)}")
    print(f"  contribution                 : {long_fomc.sum() / years:+7.1f} bps/yr "
          f"(vs {ret_bps[is_other & held].sum() / years:+.1f} bps/yr from other held days)")

    # --- would a blackout ever bind? -----------------------------------------
    exec_day = np.zeros(len(days), dtype=bool)                # signal at t -> execute t+1
    sig_any = (sig["entries"] | sig["exits"]).to_numpy()
    exec_day[1:] = sig_any[:-1]
    n_exec = int(exec_day.sum())
    on_fomc = int((exec_day & is_fomc).sum())
    near = int((exec_day & (is_fomc | is_pre | is_post)).sum())
    print(f"\nblackout relevance: {n_exec} executions total; "
          f"{on_fomc} ON an FOMC day, {near} within FOMC+-1 day")
    if on_fomc:
        idx = np.where(exec_day & is_fomc)[0]
        open_ = data["open"].astype(float)
        for i in idx:
            was_entry = bool(sig["entries"].iloc[i - 1])
            side = "BUY" if was_entry else "SELL"
            if i + 1 < len(days):
                delay = (open_.iloc[i + 1] / open_.iloc[i] - 1.0) * 1e4
                delay = delay if was_entry else -delay        # adverse = positive
                print(f"  {days[i].date()}  {side}: 1-day blackout delay would have "
                      f"cost {delay:+.1f} bps")
            else:
                print(f"  {days[i].date()}  {side}: (last bar - no next open)")


if __name__ == "__main__":
    a = sys.argv[1:]
    main(symbol=a[0] if a else "SPY", lookback=int(a[1]) if len(a) > 1 else 100)
