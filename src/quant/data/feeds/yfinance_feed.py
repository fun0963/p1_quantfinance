"""Free EOD historical data via yfinance — the default for research.

No API key required. Good enough for daily-bar strategy research; for intraday
or live data, use AlpacaFeed instead.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd

from quant.data.feeds.base import DataFeed
from quant.data.feeds.retry import with_retries
from quant.utils import get_logger

log = get_logger(__name__)

# yfinance interval strings keyed by our canonical timeframe.
_INTERVAL = {"1d": "1d", "1h": "1h", "1wk": "1wk", "1mo": "1mo"}


class YFinanceFeed(DataFeed):
    def get_history(
        self,
        symbol: str,
        start: datetime,
        end: datetime | None = None,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        # Validate input before importing the optional dep or hitting the network:
        # never silently downgrade an unknown timeframe to daily (a 5m request
        # cached under a 1d key is a silent data error). Fail loud.
        if timeframe not in _INTERVAL:
            raise ValueError(
                f"unsupported timeframe {timeframe!r} for yfinance; "
                f"supported: {sorted(_INTERVAL)}"
            )
        interval = _INTERVAL[timeframe]

        import yfinance as yf  # imported lazily so the package stays optional

        log.debug(f"yfinance fetch {symbol} {timeframe} from {start}")
        raw = with_retries(
            lambda: yf.download(
                symbol,
                start=start,
                end=end,
                interval=interval,
                auto_adjust=True,
                progress=False,
            ),
            label=f"yfinance {symbol} {timeframe}",
        )
        if raw.empty:
            raise ValueError(f"yfinance returned no data for {symbol}")

        # Normalize columns (yfinance may return a MultiIndex for single tickers).
        if isinstance(raw.columns, pd.MultiIndex):
            raw.columns = raw.columns.get_level_values(0)
        df = raw.rename(columns=str.lower)
        return self._validate(df)
