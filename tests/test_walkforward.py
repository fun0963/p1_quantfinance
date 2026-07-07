"""Tests for walk-forward analysis (offline, synthetic data)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.walkforward import summarize, walk_forward
from quant.strategies.ma_cross import MACrossStrategy


def _synthetic(n: int = 800, seed: int = 3) -> pd.DataFrame:
    idx = pd.date_range("2018-01-01", periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0.03, 1, n)), index=idx).abs() + 10
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1e6},
        index=idx,
    )


_GRID = {"fast": [5, 10], "slow": [30, 50]}


def test_walk_forward_folds_are_sequential_and_oos_follows_train():
    data = _synthetic()
    wf = walk_forward(MACrossStrategy, data, grid=_GRID,
                      train_bars=200, test_bars=60)
    assert len(wf) > 1
    # Each test window must start strictly after its own train window ends.
    assert (pd.to_datetime(wf["test_start"]) > pd.to_datetime(wf["train_end"])).all()
    # Folds advance in time (non-overlapping OOS).
    assert pd.to_datetime(wf["test_start"]).is_monotonic_increasing
    # best_params is a readable description string.
    assert wf["best_params"].str.contains("fast=").all()


def test_walk_forward_raises_when_not_enough_history():
    data = _synthetic(n=100)
    with pytest.raises(ValueError):
        walk_forward(MACrossStrategy, data, grid={"fast": [5], "slow": [20]},
                     train_bars=200, test_bars=60)


def test_walk_forward_runs_on_injected_backtrader_engine():
    """OOS evaluation must work on the event-driven engine too — this exercises
    the tz alignment (Backtrader returns a tz-naive equity index)."""
    from quant.backtest.backtrader_engine import BacktraderEngine

    data = _synthetic()
    wf = walk_forward(MACrossStrategy, data, grid=_GRID, train_bars=200, test_bars=60,
                      engine_cls=BacktraderEngine)
    assert len(wf) > 1
    assert wf["oos_sharpe"].notna().any()  # OOS scored via the injected engine


def test_summarize_keys():
    data = _synthetic()
    wf = walk_forward(MACrossStrategy, data, grid=_GRID,
                      train_bars=200, test_bars=60)
    s = summarize(wf)
    assert {"folds", "mean_is_sharpe", "mean_oos_sharpe", "wf_efficiency"} <= set(s)
    assert s["folds"] == len(wf)
