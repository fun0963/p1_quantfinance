"""Alpaca market data — historical bars (and the path to live/intraday).

Requires ALPACA_API_KEY / ALPACA_SECRET_KEY. Free IEX feed covers most research
needs; this is also the feed you'd extend with websockets for live trading.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from config import get_settings
from quant.data.feeds.base import DataFeed
from quant.data.feeds.retry import with_retries
from quant.utils import get_logger

log = get_logger(__name__)

# Map canonical timeframe -> alpaca TimeFrame, resolved lazily in get_history.
_TIMEFRAME = {"1d": ("1", "Day"), "1h": ("1", "Hour"), "1min": ("1", "Minute")}


class AlpacaFeed(DataFeed):
    def __init__(self) -> None:
        s = get_settings()
        if not s.alpaca_api_key:
            raise RuntimeError("ALPACA_API_KEY not set — see .env.example")
        self._key = s.alpaca_api_key
        self._secret = s.alpaca_secret_key

    def get_history(
        self,
        symbol: str,
        start: datetime,
        end: datetime | None = None,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        # Validate before importing the optional dep: same trap as yfinance,
        # don't silently downgrade an unknown timeframe to daily.
        if timeframe not in _TIMEFRAME:
            raise ValueError(
                f"unsupported timeframe {timeframe!r} for alpaca; "
                f"supported: {sorted(_TIMEFRAME)}"
            )
        amount, unit = _TIMEFRAME[timeframe]

        # Lazy imports keep alpaca-py optional until this feed is actually used.
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
        tf = TimeFrame(int(amount), getattr(TimeFrameUnit, unit))

        client = StockHistoricalDataClient(self._key, self._secret)
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf, start=start, end=end)
        log.debug(f"alpaca fetch {symbol} {timeframe} from {start}")
        # alpaca-py types the response as BarSet | dict; .df is the documented BarSet API.
        bars = with_retries(
            lambda: client.get_stock_bars(req).df,  # type: ignore[union-attr]
            label=f"alpaca {symbol} {timeframe}",
        )

        if bars.empty:
            raise ValueError(f"Alpaca returned no data for {symbol}")
        # Multi-symbol responses are MultiIndexed by (symbol, timestamp).
        if isinstance(bars.index, pd.MultiIndex):
            bars = bars.xs(symbol, level="symbol")
        df = bars.rename(columns=str.lower)
        return self._validate(df)
