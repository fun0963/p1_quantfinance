"""Multi-strategy portfolio: capital allocation, equity combination, weight
normalization, and JSON config parsing — all offline via injected data_map."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.portfolio import PortfolioLeg, load_portfolio_config, run_portfolio

ROOT = Path(__file__).resolve().parent.parent


def _frame(n=260, slope=0.4, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-01", periods=n, freq="D", tz="UTC")
    close = pd.Series(100 + np.arange(n) * slope + rng.normal(0, 0.5, n).cumsum(),
                      index=idx).clip(lower=1.0)
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1e6}, index=idx,
    )


def _legs():
    return [
        PortfolioLeg("SPY", "ma_cross", {"fast": 5, "slow": 20}, weight=0.5),
        PortfolioLeg("QQQ", "momentum", {"lookback": 20}, weight=0.5),
    ]


def _data():
    return {"SPY": _frame(seed=1), "QQQ": _frame(seed=2, slope=0.3)}


def test_run_portfolio_combines_legs():
    res = run_portfolio(_legs(), cash=100_000, data_map=_data())

    # Combined starts at total capital and stays finite.
    assert res.equity_curve.iloc[0] == pytest.approx(100_000, rel=0.05)
    assert np.isfinite(res.equity_curve.iloc[-1])
    # Both legs scored; correlation is a 2x2 matrix; headline metrics present.
    assert set(res.leg_metrics) == {"SPY:ma_cross", "QQQ:momentum"}
    assert res.correlation.shape == (2, 2)
    assert "sharpe" in res.metrics and "max_drawdown_pct" in res.metrics


def test_weights_are_normalized():
    legs = [PortfolioLeg("SPY", "ma_cross", {"fast": 5, "slow": 20}, weight=1),
            PortfolioLeg("QQQ", "momentum", {"lookback": 20}, weight=3)]
    res = run_portfolio(legs, cash=100_000, data_map=_data())
    weights = {f"{leg.symbol}:{leg.strategy}": leg.weight for leg in res.legs}
    assert weights["SPY:ma_cross"] == pytest.approx(0.25)
    assert weights["QQQ:momentum"] == pytest.approx(0.75)
    assert sum(leg.weight for leg in res.legs) == pytest.approx(1.0)


def test_diversification_ratio_defined_when_sharpes_exist():
    res = run_portfolio(_legs(), cash=100_000, data_map=_data())
    if res.weighted_avg_sharpe:  # both legs produced a Sharpe
        assert res.diversification_ratio is not None


def test_empty_portfolio_rejected():
    with pytest.raises(ValueError):
        run_portfolio([], data_map={})


def test_load_example_config():
    cfg = load_portfolio_config(ROOT / "portfolios" / "example.json")
    assert cfg["name"] == "balanced_2strat"
    assert cfg["cash"] == 100_000
    assert len(cfg["legs"]) == 2
    assert {leg.symbol for leg in cfg["legs"]} == {"SPY", "QQQ"}
