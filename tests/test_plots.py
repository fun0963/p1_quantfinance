"""Smoke tests for the plotly plotting layer — writes real HTML to a tmp dir."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.backtest.base import BacktestResult
from quant.backtest.plots import plot_equity, plot_heatmap


def _equity(n: int = 120, seed: int = 1) -> pd.Series:
    idx = pd.date_range("2022-01-01", periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(seed)
    return pd.Series(100_000 * (1 + rng.normal(0.001, 0.01, n)).cumprod(), index=idx)


def test_plot_equity_writes_html(tmp_path):
    res = BacktestResult(equity_curve=_equity(), engine="vectorbt")
    out = plot_equity(res, out_path=tmp_path / "eq.html")
    assert out.exists() and out.stat().st_size > 1000
    assert "<html" in out.read_text(encoding="utf-8")[:2000].lower()


def test_plot_equity_overlays_multiple(tmp_path):
    results = {
        "vectorbt": BacktestResult(equity_curve=_equity(seed=1), engine="vectorbt"),
        "backtrader": BacktestResult(equity_curve=_equity(seed=2), engine="backtrader"),
    }
    out = plot_equity(results, out_path=tmp_path / "eq2.html")
    assert out.exists() and out.stat().st_size > 1000


def test_plot_heatmap_writes_html(tmp_path):
    df = pd.DataFrame(
        {"fast": [5, 5, 10, 10], "slow": [50, 100, 50, 100],
         "sharpe": [1.0, 1.2, 0.8, 1.1],
         "total_return_pct": [10, 20, 5, 15],
         "max_drawdown_pct": [-5, -8, -4, -7], "num_trades": [3, 2, 4, 3]}
    )
    out = plot_heatmap(df, metric="sharpe", out_path=tmp_path / "hm.html")
    assert out.exists() and out.stat().st_size > 1000
