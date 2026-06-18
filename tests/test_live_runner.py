"""Tests for the live runner — latest-bar decision, dry-run vs execute, gate, reconcile."""
from __future__ import annotations

import pandas as pd

from quant.core.types import Order, OrderSide, OrderType
from quant.execution.live_runner import run_live_step
from quant.execution.paper_broker import PaperBroker
from quant.risk.gate import RiskGate, RiskLimits
from quant.strategies.base import BaseStrategy


class _LastBar(BaseStrategy):
    """Emits an entry and/or exit only on the final bar — for deterministic tests."""
    name = "lastbar"

    def __init__(self, entry_last=False, exit_last=False):
        super().__init__(entry_last=entry_last, exit_last=exit_last)
        self.entry_last, self.exit_last = entry_last, exit_last

    def generate_signals(self, data):
        e = pd.Series(False, index=data.index)
        x = pd.Series(False, index=data.index)
        if self.entry_last:
            e.iloc[-1] = True
        if self.exit_last:
            x.iloc[-1] = True
        return pd.DataFrame({"entries": e, "exits": x}, index=data.index)


def _data(last_close=100.0, n=30):
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    close = pd.Series([100.0] * (n - 1) + [last_close], index=idx)
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1e6},
        index=idx,
    )


def test_dry_run_does_not_submit():
    brk = PaperBroker(cash=100_000)
    dec = run_live_step(_LastBar(entry_last=True), _data(), "SPY", brk, dry_run=True)
    assert dec.action == "buy" and dec.qty > 0
    assert dec.order_id is None          # nothing submitted
    assert brk.position_qty("SPY") == 0  # broker untouched
    assert "dry-run" in dec.reason


def test_execute_submits_and_takes_position():
    brk = PaperBroker(cash=100_000)
    dec = run_live_step(_LastBar(entry_last=True), _data(), "SPY", brk, dry_run=False)
    assert dec.action == "buy" and dec.order_id is not None
    assert brk.position_qty("SPY") > 0


def test_exit_signal_sells_when_holding():
    brk = PaperBroker(cash=100_000)
    brk.mark("SPY", 100.0)
    brk.submit_order(Order("SPY", OrderSide.BUY, qty=100, type=OrderType.MARKET))
    dec = run_live_step(_LastBar(exit_last=True), _data(), "SPY", brk, dry_run=False)
    assert dec.action == "sell"
    assert brk.position_qty("SPY") == 0


def test_no_signal_is_flat_or_hold():
    brk = PaperBroker(cash=100_000)
    dec = run_live_step(_LastBar(), _data(), "SPY", brk, dry_run=False)
    assert dec.action == "flat" and dec.order_id is None


def test_entry_skipped_when_already_holding():
    # Position reconciliation: broker already long → entry signal must not double up.
    brk = PaperBroker(cash=100_000)
    brk.mark("SPY", 100.0)
    brk.submit_order(Order("SPY", OrderSide.BUY, qty=50, type=OrderType.MARKET))
    dec = run_live_step(_LastBar(entry_last=True), _data(), "SPY", brk, dry_run=False)
    assert dec.action == "hold" and dec.order_id is None
    assert brk.position_qty("SPY") == 50


def test_risk_gate_blocks_live_entry():
    brk = PaperBroker(cash=100_000)
    gate = RiskGate(RiskLimits(max_position_notional=1.0))
    dec = run_live_step(_LastBar(entry_last=True), _data(), "SPY", brk,
                        gate=gate, dry_run=False)
    assert dec.blocked is not None
    assert dec.order_id is None
    assert brk.position_qty("SPY") == 0


class _EntryThenHold(BaseStrategy):
    """Entry on an EARLY bar, nothing since → desired state is LONG at the latest bar."""
    name = "entryhold"

    def generate_signals(self, data):
        e = pd.Series(False, index=data.index)
        x = pd.Series(False, index=data.index)
        e.iloc[5] = True
        return pd.DataFrame({"entries": e, "exits": x}, index=data.index)


def test_target_mode_enters_when_strategy_wants_long_without_edge_today():
    # The key fix: crossover was bars ago, but target mode still gets us in.
    brk = PaperBroker(cash=100_000)
    dec = run_live_step(_EntryThenHold(), _data(), "SPY", brk, dry_run=False, mode="target")
    assert dec.target_state == "long"
    assert dec.action == "buy" and brk.position_qty("SPY") > 0


def test_signal_mode_does_nothing_between_crossovers():
    # Same strategy, signal mode: no edge on the latest bar → no action (the old gap).
    brk = PaperBroker(cash=100_000)
    dec = run_live_step(_EntryThenHold(), _data(), "SPY", brk, dry_run=False, mode="signal")
    assert dec.action == "flat" and brk.position_qty("SPY") == 0


def test_target_mode_exits_to_match_flat_state():
    class _RoundTrip(BaseStrategy):
        name = "rt"

        def generate_signals(self, data):
            e = pd.Series(False, index=data.index)
            x = pd.Series(False, index=data.index)
            e.iloc[5] = True
            x.iloc[10] = True   # entered then exited → desired FLAT now
            return pd.DataFrame({"entries": e, "exits": x}, index=data.index)

    brk = PaperBroker(cash=100_000)
    brk.mark("SPY", 100.0)
    brk.submit_order(Order("SPY", OrderSide.BUY, qty=100, type=OrderType.MARKET))
    dec = run_live_step(_RoundTrip(), _data(), "SPY", brk, dry_run=False, mode="target")
    assert dec.target_state == "flat"
    assert dec.action == "sell" and brk.position_qty("SPY") == 0


def test_target_mode_holds_when_already_aligned_long():
    # Strategy wants long and we already hold → no action (no churn).
    brk = PaperBroker(cash=100_000)
    brk.mark("SPY", 100.0)
    brk.submit_order(Order("SPY", OrderSide.BUY, qty=50, type=OrderType.MARKET))
    dec = run_live_step(_EntryThenHold(), _data(), "SPY", brk, dry_run=False, mode="target")
    assert dec.target_state == "long"
    assert dec.action == "hold" and dec.order_id is None
    assert brk.position_qty("SPY") == 50
