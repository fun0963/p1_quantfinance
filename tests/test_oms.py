"""Tests for the OMS order-lifecycle state machine and broker sync."""
from __future__ import annotations

from quant.core.types import Order, OrderSide
from quant.execution.journal import TradeJournal
from quant.execution.paper_broker import PaperBroker
from quant.ops.oms import OMS, OrderState, is_legal, map_broker_status


def _oms(tmp_path):
    return OMS(TradeJournal(db_path=tmp_path / "j.db"))


def test_on_submit_records_submitted_with_event(tmp_path):
    oms = _oms(tmp_path)
    oid = oms.on_submit(symbol="SPY", side="buy", qty=10, intended_price=100.0,
                        broker_order_id="brk-1", strategy="momentum")
    rec = oms.get(oid)
    assert rec is not None and rec.status is OrderState.SUBMITTED
    events = oms.j.order_events(oid)
    assert len(events) == 1
    assert events.iloc[0]["from_state"] == "NEW" and events.iloc[0]["to_state"] == "SUBMITTED"
    oms.j.close()


def test_legal_fill_transition_updates_fields(tmp_path):
    oms = _oms(tmp_path)
    oid = oms.on_submit(symbol="SPY", side="buy", qty=10, intended_price=100.0,
                        broker_order_id="brk-1")
    assert oms.transition(oid, OrderState.FILLED, filled_qty=10,
                          avg_fill_price=100.5, commission=0.5) is True
    rec = oms.get(oid)
    assert rec.status is OrderState.FILLED
    assert rec.filled_qty == 10 and rec.avg_fill_price == 100.5 and rec.commission == 0.5
    oms.j.close()


def test_illegal_transition_out_of_terminal_is_refused(tmp_path):
    oms = _oms(tmp_path)
    oid = oms.on_submit(symbol="SPY", side="buy", qty=1, intended_price=10.0,
                        broker_order_id="brk-1")
    oms.transition(oid, OrderState.FILLED, filled_qty=1, avg_fill_price=10.0)
    # FILLED is terminal — cannot go back to SUBMITTED.
    assert oms.transition(oid, OrderState.SUBMITTED) is False
    assert oms.get(oid).status is OrderState.FILLED     # unchanged
    oms.j.close()


def test_same_state_update_records_no_event(tmp_path):
    oms = _oms(tmp_path)
    oid = oms.on_submit(symbol="SPY", side="buy", qty=10, intended_price=10.0,
                        broker_order_id="brk-1")
    oms.transition(oid, OrderState.PARTIALLY_FILLED, filled_qty=4, avg_fill_price=10.0)
    before = len(oms.j.order_events(oid))
    # A further partial-fill update to the SAME state refreshes qty, adds no event.
    assert oms.transition(oid, OrderState.PARTIALLY_FILLED, filled_qty=7) is False
    assert len(oms.j.order_events(oid)) == before
    assert oms.get(oid).filled_qty == 7
    oms.j.close()


def test_sync_advances_paper_order_to_filled(tmp_path):
    """End-to-end: a paper broker fills synchronously; sync() flips SUBMITTED->FILLED."""
    broker = PaperBroker(cash=100_000, fees=0.0, slippage=0.0)
    broker.mark("SPY", 100.0)
    bid = broker.submit_order(Order(symbol="SPY", side=OrderSide.BUY, qty=5))

    oms = _oms(tmp_path)
    oid = oms.on_submit(symbol="SPY", side="buy", qty=5, intended_price=100.0,
                        broker_order_id=bid)
    assert oms.sync(broker) == 1
    rec = oms.get(oid)
    assert rec.status is OrderState.FILLED and rec.avg_fill_price == 100.0
    assert oms.open_orders() == []                       # nothing left open
    oms.j.close()


class _StatusBroker:
    """Broker stub whose order_status returns a fixed response, for sync() tests."""
    def __init__(self, resp):
        self._resp = resp

    def order_status(self, order_id):
        return self._resp


def test_sync_ignores_transient_status_no_illegal_transition(tmp_path):
    """A transient broker state (e.g. 'pending_cancel') on a PARTIALLY_FILLED order
    must be left as-is — not force an illegal backward move to SUBMITTED."""
    oms = _oms(tmp_path)
    oid = oms.on_submit(symbol="SPY", side="buy", qty=100, intended_price=10.0,
                        broker_order_id="brk-1")
    oms.transition(oid, OrderState.PARTIALLY_FILLED, filled_qty=40, avg_fill_price=10.0)
    before = len(oms.j.order_events(oid))
    brk = _StatusBroker({"status": "pending_cancel", "filled_qty": 40, "filled_avg_price": 10.0})
    assert oms.sync(brk) == 0                               # nothing advanced
    assert oms.get(oid).status is OrderState.PARTIALLY_FILLED
    assert len(oms.j.order_events(oid)) == before           # no spurious event/warning
    oms.j.close()


def test_filled_at_stamped_when_canceled_after_partial(tmp_path):
    """A partial fill that goes terminal (canceled) still executed shares — filled_at
    must record that execution happened."""
    oms = _oms(tmp_path)
    oid = oms.on_submit(symbol="SPY", side="buy", qty=200, intended_price=50.0,
                        broker_order_id="brk-1")
    oms.transition(oid, OrderState.CANCELED, filled_qty=120, avg_fill_price=51.30,
                   commission=2.0)
    r = oms.j.orders()
    row = r[r["id"] == oid].iloc[0]
    assert row["status"] == "CANCELED" and row["filled_qty"] == 120
    assert row["filled_at"] is not None
    oms.j.close()


def test_status_mapping_and_legality():
    assert map_broker_status("FILLED") is OrderState.FILLED
    assert map_broker_status("partially_filled") is OrderState.PARTIALLY_FILLED
    assert map_broker_status("nonsense") is None
    assert is_legal(OrderState.SUBMITTED, OrderState.FILLED)
    assert not is_legal(OrderState.FILLED, OrderState.CANCELED)   # terminal
