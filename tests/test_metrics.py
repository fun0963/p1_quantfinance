"""Tests for the trade-quality / benchmark / yearly metric helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.metrics import (
    alpha_beta,
    compute_metrics,
    monthly_returns,
    trade_stats,
    yearly_returns,
)


def test_sortino_and_calmar_present_with_drawdown():
    idx = pd.date_range("2020-01-01", periods=300, freq="B", tz="UTC")
    rng = np.random.default_rng(1)
    eq = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 300))), index=idx)
    m = compute_metrics(eq)
    assert isinstance(m["sortino"], float) and isinstance(m["calmar"], float)


def test_sortino_calmar_none_without_downside_or_drawdown():
    idx = pd.date_range("2020-01-01", periods=50, freq="B", tz="UTC")
    eq = pd.Series(np.linspace(100, 150, 50), index=idx)  # strictly increasing
    m = compute_metrics(eq)
    assert m["sortino"] is None   # no downside deviation
    assert m["calmar"] is None    # no drawdown


def test_monthly_returns_table_shape_and_coverage():
    idx = pd.date_range("2021-01-01", "2021-03-31", freq="D", tz="UTC")
    eq = pd.Series(np.linspace(100, 130, len(idx)), index=idx)
    mret = monthly_returns(eq)
    assert list(mret.index) == [2021]
    assert list(mret.columns) == list(range(1, 13))
    assert mret.loc[2021, [1, 2, 3]].notna().all()               # Jan-Mar populated
    assert mret.loc[2021, list(range(4, 13))].isna().all()       # rest blank


def test_monthly_returns_empty_when_too_short():
    assert monthly_returns(pd.Series([100.0])).empty


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


# --- PSR (probabilistic Sharpe) ----------------------------------------------
def test_psr_is_50pct_at_zero_sharpe():
    """SR=0 -> z=0 -> PSR exactly 50%, independent of higher moments."""
    from quant.backtest.metrics import _psr_pct
    rets = pd.Series([0.01, -0.01] * 100)               # mean exactly 0
    assert _psr_pct(rets) == 50.0


def test_psr_grows_with_sample_length():
    """Same positive edge, more evidence -> higher confidence the Sharpe is real."""
    from quant.backtest.metrics import _psr_pct
    rng = np.random.default_rng(7)
    rets = pd.Series(rng.normal(0.001, 0.01, 2000))
    short, long = _psr_pct(rets.iloc[:100]), _psr_pct(rets)
    assert short is not None and long is not None
    assert 0.0 <= short <= 100.0 and 0.0 <= long <= 100.0
    assert long > short


def test_compute_metrics_includes_psr():
    idx = pd.date_range("2020-01-01", periods=300, freq="B", tz="UTC")
    rng = np.random.default_rng(1)
    eq = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, 300))), index=idx)
    m = compute_metrics(eq)
    assert m["psr_pct"] is not None and 0.0 <= m["psr_pct"] <= 100.0


# --- annualized turnover -----------------------------------------------------
def test_turnover_hand_computed_round_trip():
    """1 year of flat 100k equity, one 1000-share round trip at 100:
    (100k entry + 100k exit) / 100k avg equity / 1 year = 2.0x."""
    from quant.backtest.metrics import turnover_annual
    idx = pd.date_range("2020-01-01", periods=253, freq="B", tz="UTC")  # 252 periods = 1y
    eq = pd.Series(100_000.0, index=idx)
    trades = pd.DataFrame({"Size": [1000.0], "Avg Entry Price": [100.0],
                           "Avg Exit Price": [100.0]})
    assert turnover_annual(trades, eq) == pytest.approx(2.0)


def test_turnover_open_trade_counts_entry_side_only():
    from quant.backtest.metrics import turnover_annual
    idx = pd.date_range("2020-01-01", periods=253, freq="B", tz="UTC")
    eq = pd.Series(100_000.0, index=idx)
    trades = pd.DataFrame({"Size": [1000.0], "Avg Entry Price": [100.0],
                           "Avg Exit Price": [np.nan]})                 # still open
    assert turnover_annual(trades, eq) == pytest.approx(1.0)


def test_turnover_none_for_backtrader_schema_and_empty():
    from quant.backtest.metrics import turnover_annual
    idx = pd.date_range("2020-01-01", periods=50, freq="B", tz="UTC")
    eq = pd.Series(100_000.0, index=idx)
    bt_trades = pd.DataFrame({"entry_time": [idx[1]], "entry_price": [100.0],
                              "pnl": [5.0]})                            # no Size column
    assert turnover_annual(bt_trades, eq) is None
    assert turnover_annual(None, eq) is None
    assert turnover_annual(pd.DataFrame(), eq) is None
