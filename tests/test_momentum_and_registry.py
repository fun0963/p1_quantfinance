"""Tests for the momentum strategy, the registry, and the generic sweep —
proving the research funnel is genuinely strategy-agnostic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.optimize import sweep
from quant.strategies.momentum import MomentumStrategy
from quant.strategies.registry import available, get_strategy_cls


def _synthetic(n: int = 400, seed: int = 11) -> pd.DataFrame:
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0.05, 1, n)), index=idx).abs() + 10
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1e6},
        index=idx,
    )


def test_registry_lookup_and_errors():
    assert "momentum" in available() and "ma_cross" in available()
    assert get_strategy_cls("momentum") is MomentumStrategy
    with pytest.raises(ValueError):
        get_strategy_cls("does_not_exist")


def test_momentum_validation_and_signal_contract():
    with pytest.raises(ValueError):
        MomentumStrategy(lookback=0)
    sig = MomentumStrategy(lookback=50).generate_signals(_synthetic())
    assert {"entries", "exits"} <= set(sig.columns)
    assert sig["entries"].dtype == bool
    assert not (sig["entries"] & sig["exits"]).any()  # mutually exclusive


def test_generic_sweep_drives_momentum():
    # The whole point of the refactor: sweep works on momentum with no special-casing.
    data = _synthetic()
    res = sweep(MomentumStrategy, data, grid={"lookback": [20, 50, 100], "buffer": [0.0, 0.02]})
    assert {"lookback", "buffer"} <= set(res.columns)
    assert res["sharpe"].is_monotonic_decreasing
    assert len(res) == 6  # 3 lookbacks x 2 buffers, all valid
