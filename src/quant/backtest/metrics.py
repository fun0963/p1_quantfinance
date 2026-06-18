"""Engine-agnostic performance metrics.

Both backtest engines produce an `equity_curve`; computing the headline metrics
here (rather than trusting each engine's native stats) guarantees an
apples-to-apples comparison between VectorBT and Backtrader.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Trading periods per year, keyed by canonical timeframe (for annualization).
_PERIODS_PER_YEAR = {"1d": 252, "1h": 252 * 6.5, "1wk": 52, "1mo": 12}


def compute_metrics(
    equity_curve: pd.Series,
    num_trades: int | None = None,
    timeframe: str = "1d",
) -> dict:
    """Headline metrics from an equity curve.

    Returns total return, CAGR, annualized Sharpe (rf=0), and max drawdown — all
    as percentages where applicable, rounded for display.
    """
    eq = equity_curve.dropna().astype(float)
    if len(eq) < 2:
        return {"error": "equity curve too short"}

    ppy = _PERIODS_PER_YEAR.get(timeframe, 252)
    rets = eq.pct_change(fill_method=None).dropna()

    total_return = eq.iloc[-1] / eq.iloc[0] - 1.0
    n_periods = len(eq) - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (ppy / n_periods) - 1.0 if n_periods > 0 else np.nan

    std = rets.std()
    sharpe = (rets.mean() / std) * np.sqrt(ppy) if std and std > 0 else np.nan

    drawdown = eq / eq.cummax() - 1.0
    max_dd = drawdown.min()

    return {
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2) if not np.isnan(cagr) else None,
        "sharpe": round(sharpe, 2) if not np.isnan(sharpe) else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "num_trades": num_trades,
        "final_equity": round(float(eq.iloc[-1]), 2),
    }
