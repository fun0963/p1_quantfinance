"""Tests for the trade-quality / benchmark / yearly metric helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.metrics import alpha_beta, trade_stats, yearly_returns


def test_trade_stats_win_rate_payoff_profit_factor():
    trades = pd.DataFrame({"PnL": [100.0, -50.0, 200.0, -50.0]})
    s = trade_stats(trades)
    assert s["num_wins"] == 2 and s["num_losses"] == 2
    assert s["win_rate_pct"] == 50.0
    assert s["payoff_ratio"] == 3.0      # avg win 150 / avg loss 50
    assert s["profit_factor"] == 3.0     # gross 300 / 100


def test_trade_stats_empty_or_missing_column():
    assert trade_stats(None) == {}
    assert trade_stats(pd.DataFrame()) == {}
    assert trade_stats(pd.DataFrame({"x": [1, 2]})) == {}


def test_alpha_beta_twice_the_market_has_beta_2_zero_alpha():
    idx = pd.date_range("2022-01-01", periods=120, freq="B", tz="UTC")
    bench = pd.Series(np.random.default_rng(0).normal(0.001, 0.01, 120), index=idx)
    strat = bench * 2.0                     # exactly 2x market, no excess
    ab = alpha_beta(strat, bench)
    assert ab["beta"] == 2.0
    assert abs(ab["alpha_pct"]) < 0.01


def test_alpha_beta_no_overlap_returns_empty():
    assert alpha_beta(pd.Series(dtype=float), pd.Series(dtype=float)) == {}


def test_yearly_returns_per_calendar_year():
    d2020 = pd.date_range("2020-01-01", "2020-12-31", freq="D", tz="UTC")
    d2021 = pd.date_range("2021-01-01", "2021-12-31", freq="D", tz="UTC")
    eq = pd.concat([
        pd.Series(np.linspace(100, 110, len(d2020)), index=d2020),  # +10%
        pd.Series(np.linspace(110, 99, len(d2021)), index=d2021),   # -10%
    ])
    yr = yearly_returns(eq)
    assert yr[2020] == pytest.approx(10.0, abs=0.1)
    assert yr[2021] == pytest.approx(-10.0, abs=0.1)
