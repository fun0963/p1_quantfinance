"""Local parquet cache for historical bars.

Keeps research fast and offline-friendly: download once, reload instantly.
Layout: <data_dir>/bars/<symbol>_<timeframe>.parquet. Swap this class for a
TimescaleDB-backed one later without changing callers.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from config import get_settings
from quant.data.storage.base import BarStore


class ParquetStore(BarStore):
    def __init__(self, base_dir: Path | None = None) -> None:
        self.base = (base_dir or get_settings().data_dir) / "bars"
        self.base.mkdir(parents=True, exist_ok=True)

    def _path(self, symbol: str, timeframe: str) -> Path:
        return self.base / f"{symbol}_{timeframe}.parquet"

    def save(self, symbol: str, timeframe: str, df: pd.DataFrame) -> Path:
        path = self._path(symbol, timeframe)
        df.to_parquet(path)
        return path

    def load(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        path = self._path(symbol, timeframe)
        return pd.read_parquet(path) if path.exists() else None

    def exists(self, symbol: str, timeframe: str) -> bool:
        return self._path(symbol, timeframe).exists()
