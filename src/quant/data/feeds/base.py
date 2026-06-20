"""Abstract market-data interface.

Every concrete feed (Alpaca, yfinance, ...) implements this so the rest of the
system depends on the interface, never the vendor. Returns a tidy OHLCV
DataFrame indexed by timestamp — the common shape both backtest engines accept.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime

import pandas as pd


class DataFeed(ABC):
    """Provider-agnostic source of historical OHLCV bars."""

    #: Columns every feed must return, in this order.
    COLUMNS = ["open", "high", "low", "close", "volume"]

    @abstractmethod
    def get_history(
        self,
        symbol: str,
        start: datetime,
        end: datetime | None = None,
        timeframe: str = "1d",
    ) -> pd.DataFrame:
        """Return OHLCV bars for `symbol` in [start, end].

        Index: tz-aware DatetimeIndex. Columns: self.COLUMNS.
        """

    def _validate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Enforce the contract so downstream code can trust the shape."""
        missing = set(self.COLUMNS) - set(df.columns)
        if missing:
            raise ValueError(f"{type(self).__name__} feed missing columns: {missing}")
        return df[self.COLUMNS].sort_index()
