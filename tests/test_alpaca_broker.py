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
