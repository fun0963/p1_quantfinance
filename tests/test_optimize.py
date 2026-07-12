"""Tests for the parameter sweep, incl. a regression guard on metric consistency.

The sweep must rank combos by the SAME metric definitions as a single
`VectorBTEngine` run — otherwise the 'best' Sharpe wouldn't match what a plain
backtest reports (the 252- vs 365-day annualization bug we fixed).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.backtest.optimize import sweep
from quant.backtest.vectorbt_engine import VectorBTEngine
from quant.strategies.ma_cross import MACrossStrategy


def _synthetic(n: int = 400, seed: int = 7) -> pd.DataFrame:
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0.05, 1, n)), index=idx).abs() + 10
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1e6},
        index=idx,
    )


def test_sweep_returns_only_valid_combos_ranked():
    data = _synthetic()
    res = sweep(MACrossStrategy, data, grid={"fast": [5, 10, 20], "slow": [30, 50]}, sort_by="sharpe")
    assert (res["fast"] < res["slow"]).all()          # only valid combos
    assert res["sharpe"].is_monotonic_decreasing       # ranked best-first
    assert list(res.columns) == [
        "fast", "slow", "total_return_pct", "sharpe", "max_drawdown_pct", "num_trades"
    ]


def test_sweep_metric_matches_single_backtest():
    # The headline regression test: sweep's best row == an independent backtest.
    data = _synthetic()
    res = sweep(MACrossStrategy, data, grid={"fast": [5, 10], "slow": [30, 50]}, sort_by="sharpe")
    best = res.iloc[0]

    single = VectorBTEngine().run(
        MACrossStrategy(fast=int(best.fast), slow=int(best.slow)), data
    )
    assert single.metrics["sharpe"] == best.sharpe
    assert single.metrics["total_return_pct"] == best.total_return_pct
    assert single.metrics["max_drawdown_pct"] == best.max_drawdown_pct
