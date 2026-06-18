"""Tests for the date-slicing logic in the data loader (the cache-honors-start fix)."""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from quant.data.loaders import _slice_from


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
