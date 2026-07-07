"""Backtest regression suite — golden-master metrics on a fixed synthetic series.

These lock in the numbers the engine produces today on deterministic data, so a
refactor (or a vectorbt/backtrader/pandas bump) that silently changes results
trips a test instead of slipping through. Self-contained: no network, no cache,
no committed data files — the fixture is generated from a fixed seed, so CI
reproduces it exactly.

If a change to these numbers is *intended*, update GOLDEN deliberately (and note
why in the commit) — don't loosen the tolerances to make a real regression pass.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.backtrader_engine import BacktraderEngine
from quant.backtest.vectorbt_engine import VectorBTEngine
from quant.strategies.registry import get_strategy_cls


def _fixture(n: int = 500, seed: int = 7) -> pd.DataFrame:
    """A reproducible ~2y daily series (fixed seed) — same every run / machine."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2018-01-01", periods=n, freq="B", tz="UTC")
    close = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0005, 0.01, n))), index=idx)
    return pd.DataFrame(
        {"open": close, "high": close * 1.005, "low": close * 0.995,
         "close": close, "volume": 1e6}, index=idx,
    )


# Golden metrics captured from VectorBTEngine(cash=100_000) on _fixture().
GOLDEN = {
    ("ma_cross", (("fast", 10), ("slow", 30))): {
        "total_return_pct": -2.15, "cagr_pct": -1.09, "sharpe": -0.08,
        "max_drawdown_pct": -10.84, "num_trades": 9, "final_equity": 97846.73,
    },
    ("momentum", (("lookback", 50),)): {
        "total_return_pct": -11.5, "cagr_pct": -5.98, "sharpe": -0.69,
        "max_drawdown_pct": -15.61, "num_trades": 11, "final_equity": 88496.98,
    },
}


@pytest.mark.parametrize("key", list(GOLDEN))
def test_vectorbt_metrics_match_golden(key):
    name, params = key
    strat = get_strategy_cls(name)(**dict(params))
    res = VectorBTEngine(cash=100_000).run(strat, _fixture())
    expected = GOLDEN[key]

    assert res.metrics["num_trades"] == expected["num_trades"]
    for k in ["total_return_pct", "cagr_pct", "sharpe", "max_drawdown_pct"]:
        assert res.metrics[k] == pytest.approx(expected[k], abs=0.01), k
    assert res.metrics["final_equity"] == pytest.approx(expected["final_equity"], abs=0.01)


def test_backtrader_fills_per_trade_records():
    """The Backtrader engine used to return trades=None; it now emits a per-trade
    table (for TCA / trade_stats) shaped like the VectorBT one (a 'PnL' column)."""
    from quant.backtest.metrics import trade_stats

    strat = get_strategy_cls("ma_cross")(fast=10, slow=30)
    res = BacktraderEngine(cash=100_000).run(strat, _fixture())

    assert res.trades is not None
    assert len(res.trades) == res.metrics["num_trades"] > 0
    for col in ["entry_time", "exit_time", "entry_price", "bars_held", "pnl", "PnL", "commission"]:
        assert col in res.trades.columns
    assert (res.trades["exit_time"] >= res.trades["entry_time"]).all()

    # Same 'PnL' column trade_stats already uses for VectorBT -> stats populate.
    stats = trade_stats(res.trades)
    assert "win_rate_pct" in stats
    assert stats["num_wins"] + stats["num_losses"] <= len(res.trades)


@pytest.mark.parametrize("Engine", [VectorBTEngine, BacktraderEngine])
def test_slippage_reduces_final_equity(Engine):
    """Modeling slippage must make a backtest strictly more conservative on both
    engines (no more frictionless optimism)."""
    strat = get_strategy_cls("ma_cross")(fast=10, slow=30)
    frictionless = Engine(cash=100_000, slippage=0.0).run(strat, _fixture())
    with_slip = Engine(cash=100_000, slippage=0.002).run(strat, _fixture())  # 20 bps
    assert with_slip.metrics["final_equity"] < frictionless.metrics["final_equity"]


def test_dual_engines_agree():
    """VectorBT and Backtrader must stay in lockstep (cheat-on-close) — the residual
    gap is execution modeling only. Locks the core dual-engine invariant."""
    strat = get_strategy_cls("ma_cross")(fast=10, slow=30)
    vb = VectorBTEngine(cash=100_000).run(strat, _fixture())
    bt = BacktraderEngine(cash=100_000).run(strat, _fixture())

    assert vb.metrics["total_return_pct"] == pytest.approx(bt.metrics["total_return_pct"], abs=0.2)
    assert vb.metrics["sharpe"] == pytest.approx(bt.metrics["sharpe"], abs=0.05)
    assert vb.metrics["final_equity"] == pytest.approx(bt.metrics["final_equity"], rel=0.005)
