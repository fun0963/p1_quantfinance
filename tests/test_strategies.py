"""Tests for concrete strategies and the metrics module (offline, no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.metrics import compute_metrics
from quant.strategies.ma_cross import MACrossStrategy


def _synthetic(n: int = 200, seed: int = 0) -> pd.DataFrame:
    idx = pd.date_range("2022-01-01", periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1, n)), index=idx).abs() + 10
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1e6},
        index=idx,
    )


def test_ma_cross_rejects_bad_windows():
    with pytest.raises(ValueError):
        MACrossStrategy(fast=50, slow=20)


def test_ma_cross_signal_shape_and_exclusivity():
    data = _synthetic()
    sig = MACrossStrategy(fast=5, slow=20).generate_signals(data)
    assert {"entries", "exits"} <= set(sig.columns)
    assert sig["entries"].dtype == bool and sig["exits"].dtype == bool
    # An entry and an exit can't fire on the same bar.
    assert not (sig["entries"] & sig["exits"]).any()
    # Crossovers should be relatively rare, not every bar.
    assert sig["entries"].sum() + sig["exits"].sum() < len(data)


def test_metrics_on_known_curve():
    # Monotonically rising curve: positive return, no drawdown.
    eq = pd.Series(np.linspace(100, 200, 253))
    m = compute_metrics(eq, num_trades=1, timeframe="1d")
    assert m["total_return_pct"] == 100.0
    assert m["max_drawdown_pct"] == 0.0
    assert m["num_trades"] == 1


def test_metrics_handles_short_curve():
    assert "error" in compute_metrics(pd.Series([100.0]))
