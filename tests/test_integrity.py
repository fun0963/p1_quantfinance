"""Tests for point-in-time history-mutation detection."""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from quant.data.integrity import (
    detect_history_mutation,
    read_mutation_events,
    record_mutation_event,
)


def _frame(closes, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D", tz="UTC")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame(
        {"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 1e6}, index=idx)


def test_split_rewrite_is_detected():
    old = _frame([100, 101, 102, 103, 104])
    new = _frame([50, 50.5, 51, 51.5, 52])          # 2:1 split adjusts every past bar
    rep = detect_history_mutation(old, new, symbol="AAPL")
    assert rep.mutated
    assert rep.n_overlap == 4                         # last (provisional) old bar excluded
    assert rep.n_changed == 4
    assert round(rep.max_rel_change, 2) == 0.50
    assert rep.first_changed == old.index[0].date()
    assert "likely split/adjustment" in rep.summary()


def test_unchanged_history_is_clean():
    old = _frame([100, 101, 102, 103, 104])
    rep = detect_history_mutation(old, _frame([100, 101, 102, 103, 104]), symbol="AAPL")
    assert not rep.mutated and rep.n_changed == 0 and rep.n_overlap == 4


def test_change_only_on_provisional_last_bar_is_ignored():
    old = _frame([100, 101, 102, 103, 104])
    new = _frame([100, 101, 102, 103, 109])          # only the newest bar finalised differently
    rep = detect_history_mutation(old, new, symbol="AAPL")
    assert not rep.mutated                            # settled bars 0..3 unchanged


def test_partial_revision_is_flagged():
    old = _frame([100, 101, 102, 103, 104])
    new = _frame([100, 101, 150, 103, 104])          # one settled bar rewritten
    rep = detect_history_mutation(old, new, symbol="AAPL")
    assert rep.mutated and rep.n_changed == 1
    assert rep.first_changed == old.index[2].date()
    assert "partial revision" in rep.summary()


def test_no_overlap_and_none_are_safe():
    old = _frame([100, 101, 102], start="2024-01-01")
    new = _frame([50, 51, 52], start="2025-01-01")   # disjoint date ranges
    assert detect_history_mutation(old, new).n_overlap == 0
    assert not detect_history_mutation(None, new).mutated
    assert not detect_history_mutation(old, None).mutated


def test_record_and_read_events(tmp_path):
    rep = detect_history_mutation(_frame([100, 101, 102, 103, 104]),
                                  _frame([50, 50.5, 51, 51.5, 52]),
                                  symbol="AAPL", timeframe="1d")
    path = record_mutation_event(rep, data_dir=tmp_path, at="2026-07-07T00:00:00+00:00")
    assert path is not None and path.exists()
    events = read_mutation_events(data_dir=tmp_path)
    assert len(events) == 1
    assert events.iloc[0]["symbol"] == "AAPL" and int(events.iloc[0]["n_changed"]) == 4
    # a clean report writes nothing
    clean = detect_history_mutation(_frame([1, 2, 3]), _frame([1, 2, 3]))
    assert record_mutation_event(clean, data_dir=tmp_path) is None
    assert len(read_mutation_events(data_dir=tmp_path)) == 1


def test_load_bars_records_mutation_on_redownload(tmp_path, monkeypatch):
    """Wiring: re-downloading a symbol whose history has been split-adjusted records
    a point-in-time mutation event instead of silently overwriting the cache."""
    import quant.data.integrity as integrity
    import quant.data.loaders as loaders
    from quant.data.storage.parquet_store import ParquetStore

    store = ParquetStore(base_dir=tmp_path)
    monkeypatch.setattr(loaders, "get_store", lambda: store)
    monkeypatch.setattr(integrity, "get_settings",
                        lambda: type("S", (), {"data_dir": tmp_path})())

    class _Feed:
        calls = 0

        def get_history(self, symbol, start, end=None, timeframe="1d"):
            self.calls += 1
            closes = [100, 101, 102, 103, 104] if self.calls == 1 else [50, 50.5, 51, 51.5, 52]
            return _frame(closes)

    feed = _Feed()
    loaders.load_bars("AAPL", feed, start=datetime(2024, 1, 1, tzinfo=UTC))   # first pull -> cache
    loaders.load_bars("AAPL", feed, start=datetime(2023, 1, 1, tzinfo=UTC))   # earlier start -> re-download
    events = read_mutation_events(data_dir=tmp_path)
    assert len(events) == 1 and events.iloc[0]["symbol"] == "AAPL"
