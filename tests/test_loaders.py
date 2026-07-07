"""Tests for the date-slicing + cache-coverage logic in the data loader."""
from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from quant.data.loaders import _cache_covers, _cache_fresh, _slice_from


def _frame(tz):
    idx = pd.date_range("2015-01-01", periods=2000, freq="D", tz=tz)
    return pd.DataFrame({"close": range(len(idx))}, index=idx)


def test_slice_respects_requested_start_tz_aware_index():
    df = _frame("UTC")
    out = _slice_from(df, datetime(2020, 1, 1, tzinfo=UTC))
    assert out.index[0].date() >= datetime(2020, 1, 1).date()
    assert out.index[0].year == 2020


def test_slice_handles_naive_index_with_aware_start():
    df = _frame(None)  # tz-naive index
    out = _slice_from(df, datetime(2020, 1, 1, tzinfo=UTC))  # aware start
    assert len(out) < len(df)
    assert out.index[0].year == 2020


def test_slice_keeps_all_when_start_before_data():
    df = _frame("UTC")
    out = _slice_from(df, datetime(2010, 1, 1, tzinfo=UTC))
    assert len(out) == len(df)


def test_cache_covers_exact_and_nontrading_gap():
    assert _cache_covers(date(2020, 1, 1), date(2020, 1, 1))    # exact start
    assert _cache_covers(date(2020, 1, 2), date(2020, 1, 1))    # 1-day holiday gap (the real-world case)
    assert _cache_covers(date(2019, 12, 30), date(2020, 1, 1))  # cache reaches further back


def test_cache_does_not_cover_genuinely_missing_history():
    # Cache starts a full year after the request -> re-download to get earlier bars.
    assert not _cache_covers(date(2021, 1, 1), date(2020, 1, 1))


def test_cache_freshness_tolerance():
    # last bar 1 day before "now", tolerance 1 -> still fresh
    assert _cache_fresh(date(2024, 1, 10), date(2024, 1, 11), 1)
    # last bar 10 days old, tolerance 3 -> stale (live must re-download)
    assert not _cache_fresh(date(2024, 1, 1), date(2024, 1, 11), 3)
