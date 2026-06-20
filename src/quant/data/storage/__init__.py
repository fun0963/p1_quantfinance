"""Bar storage backends + a config-driven factory.

Callers use `get_store()` rather than instantiating a backend directly, so the
choice (local parquet vs TimescaleDB) is driven by `STORAGE_BACKEND` in config
and can be changed without touching any caller.
"""
from __future__ import annotations

from pathlib import Path

from config import get_settings
from quant.data.storage.base import BarStore
from quant.data.storage.parquet_store import ParquetStore

__all__ = ["BarStore", "ParquetStore", "get_store"]


def get_store(base_dir: Path | None = None) -> BarStore:
    """Return the configured bar store. `parquet` (default) or `timescale`."""
    backend = get_settings().storage_backend.lower()
    if backend == "timescale":
        from quant.data.storage.timescale_store import TimescaleStore  # lazy: optional driver

        return TimescaleStore()
    if backend == "parquet":
        return ParquetStore(base_dir)
    raise ValueError(f"unknown STORAGE_BACKEND {backend!r} (use 'parquet' or 'timescale')")
