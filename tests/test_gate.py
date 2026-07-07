"""Risk gate — especially that risk-REDUCING orders are never blocked by the
automated limits (P0 #5: you must always be able to cut risk / stop out)."""
from __future__ import annotations

from quant.core.types import Order, OrderSide, OrderType
from quant.risk.gate import RiskGate, RiskLimits


def _order(side, qty=10):
    return Order("SPY", side, qty=qty, type=OrderType.MARKET)


def test_daily_loss_blocks_new_risk_but_allows_protective_exit():
    gate = RiskGate(RiskLimits(max_daily_loss=1000))
    gate.report_daily_pnl(-2000)  # breaker tripped
    # A BUY (adds risk) is blocked...
    assert gate.check_order(_order(OrderSide.BUY), price=100, current_position_qty=0) is not None
    # ...but a SELL that reduces the long position must pass (the stop-loss must fire).
    assert gate.check_order(_order(OrderSide.SELL), price=100, current_position_qty=10) is None


def test_position_cap_allows_reducing_order():
    gate = RiskGate(RiskLimits(max_position_notional=500))
    # Already over the cap (1000 held); selling to reduce must be allowed.
    assert gate.check_order(_order(OrderSide.SELL, qty=5), price=100, current_position_qty=10) is None
    # Buying more (increasing) stays blocked.
    assert gate.check_order(_order(OrderSide.BUY, qty=5), price=100, current_position_qty=10) is not None


def test_manual_kill_switch_blocks_even_reducing_orders():
    gate = RiskGate(RiskLimits(locked=True))
    # The manual lock is absolute — it stops even a risk-reducing sell.
    assert gate.check_order(_order(OrderSide.SELL), price=100, current_position_qty=10) is not None
