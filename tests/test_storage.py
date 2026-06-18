"""Storage layer: the backend factory, the BarStore contract, and (optionally)
a live TimescaleDB round-trip. The DB test is skipped unless TIMESCALE_TEST_DSN
is set, so the default suite stays offline and dependency-free."""
from __future__ import annotations

import os

import pandas as pd
import pytest

from quant.data.storage import BarStore, ParquetStore, get_store
from quant.data.storage.timescale_store import TimescaleStore


def _bars(n=10):
    idx = pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")
    close = pd.Series(range(100, 100 + n), index=idx, dtype=float)
    return pd.DataFrame(
        {"open": close, "high": close + 1, "low": close - 1, "close": close,
         "volume": 1_000.0}, index=idx,
    )


def test_parquet_store_is_a_barstore():
    assert issubclass(ParquetStore, BarStore)
    assert issubclass(TimescaleStore, BarStore)


def test_get_store_defaults_to_parquet():
    assert isinstance(get_store(), ParquetStore)


def test_parquet_roundtrip(tmp_path):
    store = ParquetStore(base_dir=tmp_path)
    assert store.exists("SPY", "1d") is False
    store.save("SPY", "1d", _bars())
    assert store.exists("SPY", "1d") is True
    out = store.load("SPY", "1d")
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    assert len(out) == 10


def test_get_store_rejects_unknown_backend(monkeypatch):
    import quant.data.storage as st

    monkeypatch.setattr(st, "get_settings", lambda: type("S", (), {"storage_backend": "bogus"})())
    with pytest.raises(ValueError):
        st.get_store()


def test_timescale_store_unreachable_db_raises_clear_error():
    # Port 1 refuses instantly; works whether or not psycopg is installed
    # (missing driver -> RuntimeError; refused connection -> RuntimeError).
    with pytest.raises(RuntimeError):
        TimescaleStore(dsn="postgresql://nouser:nopass@127.0.0.1:1/nodb")


@pytest.mark.skipif(not os.getenv("TIMESCALE_TEST_DSN"), reason="needs a live TimescaleDB")
def test_timescale_roundtrip():
    store = TimescaleStore(dsn=os.environ["TIMESCALE_TEST_DSN"])
    store.save("TST", "1d", _bars())
    assert store.exists("TST", "1d") is True
    out = store.load("TST", "1d")
    assert len(out) == 10
    assert list(out.columns) == ["open", "high", "low", "close", "volume"]
    # Idempotent upsert: saving again doesn't duplicate rows.
    store.save("TST", "1d", _bars())
    assert len(store.load("TST", "1d")) == 10
