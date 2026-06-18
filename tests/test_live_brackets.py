"""Tests for live bracket/OCO support — price math, Alpaca request construction,
and the live runner's bracket entry path. All offline (no network)."""
from __future__ import annotations

import pandas as pd
from pytest import approx

from quant.execution.alpaca_broker import bracket_buy_request, oco_sell_request
from quant.execution.live_runner import run_live_step
from quant.execution.paper_broker import PaperBroker
from quant.risk.bracket import BracketConfig, bracket_prices


# --- price math -------------------------------------------------------------
def test_bracket_prices_both_legs():
    stop, take = bracket_prices(100.0, 0.05, 0.10)
    assert stop == approx(95.0) and take == approx(110.0)


def test_bracket_prices_single_leg_disables_other():
    assert bracket_prices(100.0, 0.0, 0.10) == (None, approx(110.0))
    assert bracket_prices(100.0, 0.05, 0.0) == (approx(95.0), None)


# --- Alpaca request construction (objects only, no submission) --------------
def test_bracket_buy_request_shape():
    from alpaca.trading.enums import OrderClass, OrderSide

    req = bracket_buy_request("SPY", 10, stop_price=95.0, take_price=110.0)
    assert req.order_class == OrderClass.BRACKET
    assert req.side == OrderSide.BUY
    assert req.stop_loss.stop_price == approx(95.0)
    assert req.take_profit.limit_price == approx(110.0)


def test_oco_sell_request_shape():
    from alpaca.trading.enums import OrderClass, OrderSide

    req = oco_sell_request("SPY", 10, stop_price=95.0, take_price=110.0)
    assert req.order_class == OrderClass.OCO
    assert req.side == OrderSide.SELL
    # OCO must carry both legs as request objects (Alpaca: oco requires take_profit.limit_price)
    assert req.take_profit.limit_price == approx(110.0)
    assert req.stop_loss.stop_price == approx(95.0)


# --- live runner bracket entry path -----------------------------------------
from quant.strategies.base import BaseStrategy  # noqa: E402


class _LastBarEntry(BaseStrategy):
    name = "lbe"

    def generate_signals(self, data):
        e = pd.Series(False, index=data.index)
        e.iloc[-1] = True
        return pd.DataFrame({"entries": e, "exits": pd.Series(False, index=data.index)},
                            index=data.index)


class _BracketBroker(PaperBroker):
    """Paper broker that also records server-side bracket calls (for the test)."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.bracket_calls = []

    def submit_bracket_buy(self, symbol, qty, stop_price, take_price):
        self.bracket_calls.append((symbol, qty, stop_price, take_price))
        return "bracket-1"


def _data(last_close=100.0, n=30):
    idx = pd.date_range("2024-01-01", periods=n, freq="D", tz="UTC")
    close = pd.Series([100.0] * (n - 1) + [last_close], index=idx)
    return pd.DataFrame(
        {"open": close, "high": close, "low": close, "close": close, "volume": 1e6},
        index=idx,
    )


def test_live_entry_routes_through_bracket_when_supported():
    brk = _BracketBroker(cash=100_000)
    cfg = BracketConfig(stop_pct=0.05, take_pct=0.10)
    dec = run_live_step(_LastBarEntry(), _data(last_close=100.0), "SPY", brk,
                        dry_run=False, bracket_cfg=cfg)
    assert dec.order_id == "bracket-1"
    assert len(brk.bracket_calls) == 1
    _, qty, stop, take = brk.bracket_calls[0]
    assert stop == approx(95.0) and take == approx(110.0)
    assert float(qty).is_integer()        # bracket orders need whole shares


def test_bracket_entry_floors_to_whole_shares():
    # price 753.20, 95% of 100k -> 126.13 shares -> bracket must use 126 (whole).
    brk = _BracketBroker(cash=100_000)
    cfg = BracketConfig(stop_pct=0.05, take_pct=0.10)
    dec = run_live_step(_LastBarEntry(), _data(last_close=753.20), "SPY", brk,
                        dry_run=False, bracket_cfg=cfg)
    _, qty, _, _ = brk.bracket_calls[0]
    assert qty == 126
    assert dec.qty == 126


def test_live_entry_plain_when_broker_lacks_bracket_support():
    # Vanilla PaperBroker has no submit_bracket_buy → falls back to a normal order.
    brk = PaperBroker(cash=100_000)
    cfg = BracketConfig(stop_pct=0.05, take_pct=0.10)
    dec = run_live_step(_LastBarEntry(), _data(last_close=100.0), "SPY", brk,
                        dry_run=False, bracket_cfg=cfg)
    assert dec.order_id is not None and dec.order_id.startswith("paper-")
    assert brk.position_qty("SPY") > 0
