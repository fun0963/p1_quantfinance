"""Tests for transaction cost analysis (slippage + commission over OMS orders)."""
from __future__ import annotations

import math

from quant.execution.journal import TradeJournal
from quant.ops.oms import OMS, OrderState
from quant.ops.tca import slippage_bps, tca_report


def _fill(oms, side, qty, intended, fill, commission):
    oid = oms.on_submit(symbol="SPY", side=side, qty=qty, intended_price=intended,
                        broker_order_id=f"brk-{side}-{intended}", strategy="momentum")
    oms.transition(oid, OrderState.FILLED, filled_qty=qty, avg_fill_price=fill,
                   commission=commission)
    return oid


def test_slippage_sign_convention():
    # Buying above the intended price is adverse (positive); below is improvement.
    assert slippage_bps(100.0, 101.0, "buy") == 100.0
    assert slippage_bps(100.0, 99.0, "buy") == -100.0
    # Selling below intended is adverse (positive).
    assert slippage_bps(100.0, 99.0, "sell") == 100.0
    assert math.isnan(slippage_bps(0.0, 10.0, "buy"))       # no benchmark -> NaN


def test_tca_report_aggregates(tmp_path):
    oms = OMS(TradeJournal(db_path=tmp_path / "j.db"))
    _fill(oms, "buy", 10, intended=100.0, fill=101.0, commission=1.0)   # +100 bps, $10 slip
    _fill(oms, "sell", 10, intended=100.0, fill=99.0, commission=1.0)   # +100 bps, $10 slip
    # A third order left working (not filled) -> counts against the fill rate.
    oms.on_submit(symbol="SPY", side="buy", qty=1, intended_price=50.0,
                  broker_order_id="brk-open", strategy="momentum")

    rep = tca_report(oms.j)
    assert rep.n_orders == 3 and rep.n_filled == 2
    assert rep.fill_rate == 2 / 3
    assert rep.avg_slippage_bps == 100.0
    assert rep.total_slippage_usd == 20.0
    assert rep.total_commission_usd == 2.0
    assert rep.total_cost_usd == 22.0
    assert rep.total_notional_usd == 101.0 * 10 + 99.0 * 10
    assert "avg slippage +100.0 bps" in rep.summary()
    oms.j.close()


def test_tca_empty_is_safe(tmp_path):
    with TradeJournal(db_path=tmp_path / "j.db") as tj:
        rep = tca_report(tj)
    assert rep.n_orders == 0 and rep.n_filled == 0
    assert "no execution cost yet" in rep.summary()


def test_tca_strategy_filter(tmp_path):
    oms = OMS(TradeJournal(db_path=tmp_path / "j.db"))
    _fill(oms, "buy", 10, intended=100.0, fill=101.0, commission=0.0)   # strategy=momentum
    rep = tca_report(oms.j, strategy="other")
    assert rep.n_orders == 0                                # filtered out
    oms.j.close()


def test_partial_then_canceled_is_still_costed(tmp_path):
    """A partial fill that is later canceled still executed real shares — its slippage
    and commission must appear in TCA, not vanish because status != FILLED."""
    oms = OMS(TradeJournal(db_path=tmp_path / "j.db"))
    oid = oms.on_submit(symbol="SPY", side="buy", qty=200, intended_price=50.0,
                        broker_order_id="brk-p", strategy="momentum")
    # 120/200 fill @ 51.30, then the remainder is canceled (broker carries the fill).
    oms.transition(oid, OrderState.CANCELED, filled_qty=120, avg_fill_price=51.30,
                   commission=2.0)
    rep = tca_report(oms.j)
    assert rep.n_filled == 1                                # counted despite CANCELED status
    assert rep.total_commission_usd == 2.0
    # slippage = (51.30 - 50.0)/50.0 * 1e4 = 260 bps on the 120 executed shares
    assert round(rep.avg_slippage_bps, 1) == 260.0
    assert round(rep.total_slippage_usd, 2) == round((51.30 - 50.0) * 120, 2)
    oms.j.close()
