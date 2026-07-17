"""Feeds must reject unknown timeframes loudly instead of silently downgrading
to daily (a 5m request cached under a 1d key is a silent data error).

The validation happens before the optional vendor import and before any network
call, so these run offline without yfinance / alpaca-py installed.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from quant.data.feeds.yfinance_feed import YFinanceFeed

START = datetime(2020, 1, 1, tzinfo=UTC)


def test_yfinance_rejects_unknown_timeframe():
    with pytest.raises(ValueError, match="unsupported timeframe"):
        YFinanceFeed().get_history("SPY", start=START, timeframe="5m")


def test_yfinance_error_lists_supported_timeframes():
    with pytest.raises(ValueError, match="1d"):
        YFinanceFeed().get_history("SPY", start=START, timeframe="bogus")


def test_alpaca_rejects_unknown_timeframe(monkeypatch):
    import quant.data.feeds.alpaca_feed as af

    # Bypass the API-key check in __init__ without touching real settings.
    monkeypatch.setattr(
        af, "get_settings",
        lambda: type("S", (), {"alpaca_api_key": "k", "alpaca_secret_key": "s"})(),
    )
    with pytest.raises(ValueError, match="unsupported timeframe"):
        af.AlpacaFeed().get_history("SPY", start=START, timeframe="5m")


@pytest.mark.parametrize("timeframe,expected", [("1min", "iex"), ("1d", "sip")])
def test_alpaca_feed_choice_by_timeframe(monkeypatch, timeframe, expected):
    """Regression: the free plan's SIP historical feed withholds the last 15
    minutes, so intraday requests must pin IEX or the newest minute bar always
    fails the live freshness gate (941s-old bar vs 300s, seen in production)."""
    pytest.importorskip("alpaca")
    import alpaca.data.historical as hist

    import quant.data.feeds.alpaca_feed as af

    captured = {}

    class _StubClient:
        def __init__(self, *a, **k):
            pass

        def get_stock_bars(self, req):
            captured["feed"] = req.feed
            raise ValueError("captured - stop here")   # fatal for with_retries: no retry

    monkeypatch.setattr(hist, "StockHistoricalDataClient", _StubClient)
    monkeypatch.setattr(
        af, "get_settings",
        lambda: type("S", (), {"alpaca_api_key": "k", "alpaca_secret_key": "s"})(),
    )
    with pytest.raises(ValueError, match="captured"):
        af.AlpacaFeed().get_history("QQQ", start=START, timeframe=timeframe)
    assert str(captured["feed"]).split(".")[-1].lower() == expected
