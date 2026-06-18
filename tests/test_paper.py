"""Phase 3 tests: risk gate, paper broker, and the end-to-end paper session."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.core.types import Order, OrderSide, OrderType
from quant.execution.paper_broker import PaperBroker
from quant.execution.session import run_paper_session
from quant.risk.gate import RiskGate, RiskLimits
from quant.strategies.ma_cross import MACrossStrategy


def _buy(qty=10.0):
    return Order(symbol="SPY", side=OrderSide.BUY, qty=qty, type=OrderType.MARKET)


# --- RiskGate ---------------------------------------------------------------
def test_gate_lock_blocks_everything():
    gate = RiskGate(RiskLimits(enabled=False, locked=True))  # lock overrides disabled
    assert gate.check_order(_buy(), price=100) is not None


def test_gate_per_order_caps():
    gate = RiskGate(RiskLimits(max_order_qty=5))
    assert gate.check_order(_buy(qty=10), price=100) is not None
    assert gate.check_order(_buy(qty=5), price=100) is None

    gate = RiskGate(RiskLimits(max_order_notional=500))
    assert gate.check_order(_buy(qty=10), price=100) is not None  # 1000 > 500


def test_gate_position_and_daily_loss():
    gate = RiskGate(RiskLimits(max_position_notional=1500))
    # already holding 10 @ 100; buying 10 more -> 20*100 = 2000 > 1500
    assert gate.check_order(_buy(qty=10), price=100, current_position_qty=10) is not None

    gate = RiskGate(RiskLimits(max_daily_loss=1000))
    gate.report_daily_pnl(-1200)
    assert gate.check_order(_buy(), price=100) is not None
    gate.report_daily_pnl(-500)
    assert gate.check_order(_buy(), price=100) is None


# --- PaperBroker ------------------------------------------------------------
def test_paper_broker_buy_then_sell_roundtrip():
    b = PaperBroker(cash=100_000, fees=0.0)
    b.mark("SPY", 100.0)
    b.submit_order(_buy(qty=10))
    assert b.position_qty("SPY") == 10
    assert b.get_cash() == 100_000 - 1000
    assert b.equity() == 100_000  # no fees, mark unchanged

    b.submit_order(Order(symbol="SPY", side=OrderSide.SELL, qty=10, type=OrderType.MARKET))
    assert b.position_qty("SPY") == 0
    assert b.get_cash() == 100_000
    assert b.get_positions() == []


def test_paper_broker_requires_marked_price():
    b = PaperBroker()
    try:
        b.submit_order(_buy())
    except RuntimeError as e:
        assert "marked price" in str(e)
    else:
        raise AssertionError("expected RuntimeError for unmarked symbol")


# --- end-to-end session -----------------------------------------------------
def _synthetic(n=300, seed=5):
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0.05, 1, n)), index=idx).abs() + 10
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1e6}, index=idx,
    )


def test_paper_session_runs_and_trades():
    data = _synthetic()
    res = run_paper_session(MACrossStrategy(fast=5, slow=20), data, "SPY")
    assert len(res.equity_curve) == len(data)
    assert len(res.fills) > 0          # the strategy actually traded
    assert "sharpe" in res.metrics


def test_paper_session_risk_gate_blocks_entries():
    data = _synthetic()
    # Position cap of $1 makes every BUY breach the gate → no fills, all blocked.
    gate = RiskGate(RiskLimits(max_position_notional=1.0))
    res = run_paper_session(MACrossStrategy(fast=5, slow=20), data, "SPY", gate=gate)
    buys = [f for f in res.fills if f.side is OrderSide.BUY]
    assert buys == []
    assert len(res.blocked) > 0


class _ScriptedStrategy(MACrossStrategy):
    """Entry on bar 1, exit on bar 2 — for a deterministic kill-switch test."""

    name = "scripted"

    def generate_signals(self, data):
        entries = pd.Series(False, index=data.index)
        exits = pd.Series(False, index=data.index)
        entries.iloc[1] = True
        exits.iloc[2] = True
        return pd.DataFrame({"entries": entries, "exits": exits}, index=data.index)


def test_daily_loss_killswitch_fires_on_single_day_crash():
    # Hold a big position into a -40% day; the daily-loss gate must block the exit.
    idx = pd.date_range("2022-01-03", periods=4, freq="D", tz="UTC")
    close = pd.Series([100.0, 100.0, 60.0, 60.0], index=idx)
    data = pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1e6},
        index=idx,
    )
    gate = RiskGate(RiskLimits(max_daily_loss=10_000))
    res = run_paper_session(_ScriptedStrategy(fast=2, slow=3), data, "SPY", gate=gate)

    assert any("daily loss" in reason for _, reason in res.blocked)
