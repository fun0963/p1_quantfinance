"""AlpacaBroker client reuse. Skipped unless alpaca-py is installed. Offline:
TradingClient construction does not hit the network, and settings are stubbed so
no real credentials are needed."""
from __future__ import annotations

import pytest

pytest.importorskip("alpaca")

import quant.execution.alpaca_broker as ab  # noqa: E402


def _stub_settings(monkeypatch):
    monkeypatch.setattr(
        ab, "get_settings",
        lambda: type("S", (), {"alpaca_paper": True, "alpaca_api_key": "k",
                               "alpaca_secret_key": "s"})(),
    )


def test_client_is_reused_within_a_broker(monkeypatch):
    _stub_settings(monkeypatch)
    broker = ab.AlpacaBroker()
    c1 = broker._client()
    c2 = broker._client()
    assert c1 is c2  # one TradingClient per broker, not one per call


def test_each_broker_has_its_own_client(monkeypatch):
    _stub_settings(monkeypatch)
    a, b = ab.AlpacaBroker(), ab.AlpacaBroker()
    assert a._client() is not b._client()


def test_get_open_orders_normalizes_enum_side(monkeypatch):
    """Regression: str(OrderSide.SELL) is 'OrderSide.SELL', so a naive .lower()
    yields 'orderside.sell' and every side == 'sell'/'buy' check upstream
    (reconcile's protected-position check, the live open-buy guard) silently
    fails. Caught in the first real ops run against Alpaca paper."""
    from types import SimpleNamespace

    from alpaca.trading.enums import OrderSide as ASide

    _stub_settings(monkeypatch)
    broker = ab.AlpacaBroker()
    fake_orders = [
        SimpleNamespace(id="oco-1", symbol="SPY", side=ASide.SELL, qty="126"),
        SimpleNamespace(id="buy-1", symbol="QQQ", side=ASide.BUY, qty=None),
    ]
    broker._cached_client = SimpleNamespace(get_orders=lambda filter: fake_orders)

    out = broker.get_open_orders()
    assert [o["side"] for o in out] == ["sell", "buy"]      # plain values, not "orderside.sell"
    assert out[0]["qty"] == 126.0 and out[1]["qty"] == 0.0

    # The two real consumers now match:
    assert any(o["side"] == "sell" for o in out)            # reconcile's protected check
    assert any(o["side"] == "buy" for o in out)             # live_runner._has_open_buy
