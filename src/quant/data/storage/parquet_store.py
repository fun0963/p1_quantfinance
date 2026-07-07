"""Local parquet cache for historical bars.

Keeps research fast and offline-friendly: download once, reload instantly.
Layout: <data_dir>/bars/<symbol>_<timeframe>.parquet. Swap this class for a
TimescaleDB-backed one later without changing callers.
"""
from __future__ import annotations

import os
import shutil
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
        """Persist bars atomically, keeping one backup generation.

        Write to a temp sibling then os.replace() it over the destination, so a
        crash mid-write can never leave a half-written parquet in place. Before
        replacing, copy the existing file to a `.bak` so a bad re-download (see
        integrity.py) doesn't permanently destroy good history.
        """
        path = self._path(symbol, timeframe)
        tmp = path.with_name(path.name + ".tmp")
        try:
            df.to_parquet(tmp)
            if path.exists():
                shutil.copy2(path, path.with_name(path.name + ".bak"))
            os.replace(tmp, path)  # atomic rename over the destination
        finally:
            if tmp.exists():  # only left behind if to_parquet/copy failed
                tmp.unlink()
        return path

    def load(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        path = self._path(symbol, timeframe)
        return pd.read_parquet(path) if path.exists() else None

    def exists(self, symbol: str, timeframe: str) -> bool:
        return self._path(symbol, timeframe).exists()
