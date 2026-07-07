"""Walk-forward analysis — the honest test of a parameter sweep, strategy-agnostic.

Optimizing parameters on all history then reporting that same history is
curve-fitting. Walk-forward instead: optimize on a *train* window, then measure
performance on the *next, unseen* test window, and roll forward. Aggregated
out-of-sample (OOS) results are the trustworthy estimate; the gap between
in-sample (IS) and OOS Sharpe is the overfitting tax.

Warmup handling: each OOS slice is prefixed with the chosen strategy's
`warmup_bars()` of history so its indicators are already warm at the test
window's first bar — otherwise they would be NaN and it couldn't trade early.
"""
from __future__ import annotations

import pandas as pd

from quant.backtest.base import BacktestEngine
from quant.backtest.metrics import compute_metrics
from quant.backtest.optimize import sweep
from quant.backtest.vectorbt_engine import VectorBTEngine
from quant.strategies.base import BaseStrategy
from quant.utils import get_logger

log = get_logger(__name__)

_METRIC_COLS = {"total_return_pct", "sharpe", "max_drawdown_pct", "num_trades"}


def _best_params(sweep_row: pd.Series) -> dict:
    """Extract just the strategy parameters from a ranked sweep row."""
    return {k: v for k, v in sweep_row.items() if k not in _METRIC_COLS}


def walk_forward(
    strategy_cls: type[BaseStrategy],
    data: pd.DataFrame,
    grid: dict[str, list] | None = None,
    train_bars: int = 504,     # ~2 trading years
    test_bars: int = 126,      # ~6 trading months
    sort_by: str = "sharpe",
    timeframe: str = "1d",
    engine_cls: type[BacktestEngine] = VectorBTEngine,
) -> pd.DataFrame:
    """Rolling-window walk-forward for any strategy. One row per fold.

    `engine_cls` runs the out-of-sample evaluation (default VectorBT); pass
    BacktraderEngine to score OOS on the event-driven, live-like engine. The
    in-sample optimization always uses the vectorized `sweep` — scanning a grid
    is VectorBT's job regardless of which engine confirms the survivor.
    """
    n = len(data)
    if train_bars + test_bars > n:
        raise ValueError(
            f"need >= {train_bars + test_bars} bars, got {n} "
            "(shorten train/test or fetch more history)"
        )
    grid = grid or strategy_cls.default_grid()

    rows = []
    fold = 0
    start = 0
    while start + train_bars + test_bars <= n:
        train = data.iloc[start : start + train_bars]
        test_start = start + train_bars
        test_end = test_start + test_bars

        # 1) Optimize on the train window (in-sample).
        ranked = sweep(strategy_cls, train, grid=grid, sort_by=sort_by, timeframe=timeframe)
        best = ranked.iloc[0]
        params = _best_params(best)
        strat = strategy_cls(**params)

        # 2) Evaluate on the unseen test window (out-of-sample), warmed up.
        warmup = strat.warmup_bars()
        lo = max(0, test_start - warmup)
        oos_slice = data.iloc[lo:test_end]
        res = engine_cls().run(strat, oos_slice, timeframe=timeframe)

        test_first_ts = data.index[test_start]
        # Engines differ on index tz (Backtrader returns tz-naive); align to the
        # data's tz before slicing by the tz-aware test timestamp.
        eq = res.equity_curve
        if eq.index.tz is None and data.index.tz is not None:
            eq = eq.tz_localize(data.index.tz)
        oos_equity = eq.loc[test_first_ts:]
        oos_entries = int(strat.generate_signals(oos_slice)["entries"].loc[test_first_ts:].sum())
        oos = compute_metrics(oos_equity, num_trades=oos_entries, timeframe=timeframe)

        rows.append(
            {
                "fold": fold,
                "train_start": train.index[0].date(),
                "train_end": train.index[-1].date(),
                "test_start": test_first_ts.date(),
                "test_end": data.index[test_end - 1].date(),
                "best_params": ", ".join(f"{k}={v}" for k, v in params.items()),
                "is_sharpe": best.sharpe,                 # in-sample (optimistic)
                "oos_sharpe": oos["sharpe"],              # out-of-sample (honest)
                "oos_return_pct": oos["total_return_pct"],
                "oos_max_dd_pct": oos["max_drawdown_pct"],
                "oos_trades": oos["num_trades"],
            }
        )
        fold += 1
        start += test_bars  # non-overlapping OOS windows

    return pd.DataFrame(rows)


def summarize(wf: pd.DataFrame) -> dict:
    """Aggregate walk-forward folds into headline robustness numbers."""
    is_sharpe = wf["is_sharpe"].mean()
    oos_sharpe = wf["oos_sharpe"].mean()
    # Walk-forward efficiency: OOS / IS. ~1 = robust; «1 = overfit; <0 = broken.
    eff = (oos_sharpe / is_sharpe) if is_sharpe else float("nan")
    return {
        "folds": len(wf),
        "mean_is_sharpe": round(is_sharpe, 3),
        "mean_oos_sharpe": round(oos_sharpe, 3),
        "wf_efficiency": round(eff, 2),
        "oos_sharpe_pos_rate": round((wf["oos_sharpe"] > 0).mean(), 2),
        "mean_oos_return_pct": round(wf["oos_return_pct"].mean(), 2),
    }
