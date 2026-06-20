"""Bar-store abstraction.

A `BarStore` persists and serves OHLCV history keyed by (symbol, timeframe).
The rest of the system (loaders, CLI) depends on *this interface*, never on a
concrete backend — so swapping the local parquet cache for TimescaleDB is a
config flip, not a code change. See `get_store()` in this package's __init__.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class BarStore(ABC):
    """Persist / serve OHLCV bars keyed by (symbol, timeframe).

    Implementations must round-trip the feed's shape: a tz-aware DatetimeIndex
    and the OHLCV columns (open/high/low/close/volume), so a frame saved and
    reloaded is equivalent for backtesting purposes.
    """

    @abstractmethod
    def save(self, symbol: str, timeframe: str, df: pd.DataFrame):
        """Persist `df` for (symbol, timeframe). Returns a backend-specific handle
        (a Path for parquet, a short description for a database) for logging."""

    @abstractmethod
    def load(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        """Return stored bars for (symbol, timeframe), or None if absent."""

    @abstractmethod
    def exists(self, symbol: str, timeframe: str) -> bool:
        """Whether any bars are stored for (symbol, timeframe)."""
