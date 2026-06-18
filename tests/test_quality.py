"""Tests for data-quality checks."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.data.quality import check_bars


def _clean(n: int = 60) -> pd.DataFrame:
    idx = pd.date_range("2022-01-03", periods=n, freq="B", tz="UTC")  # business days
    close = pd.Series(np.linspace(100, 120, n), index=idx)
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1e6},
        index=idx,
    )


def test_clean_data_passes():
    rep = check_bars(_clean())
    assert rep.ok
    assert not rep.issues


def test_detects_nans_and_nonpositive_and_ohlc():
    df = _clean()
    df.loc[df.index[5], "close"] = np.nan          # NaN
    df.loc[df.index[6], "open"] = -1.0             # non-positive
    df.loc[df.index[7], "high"] = df["low"].iloc[7] - 1  # high < low
    rep = check_bars(df)
    assert not rep.ok
    joined = " ".join(rep.issues)
    assert "NaN" in joined
    assert "non-positive" in joined
    assert "OHLC" in joined


def test_detects_duplicate_and_unsorted_index():
    df = _clean()
    df = pd.concat([df, df.iloc[[10]]])            # duplicate timestamp
    rep = check_bars(df)
    assert any("duplicate" in m for m in rep.issues)


def test_split_jump_is_a_warning_not_an_issue():
    df = _clean()
    # Drop every price to a third from bar 30 on → ~67% close-to-close drop
    # (unadjusted split fingerprint), comfortably past the 50% threshold.
    df.iloc[30:] = df.iloc[30:] / 3
    rep = check_bars(df)
    assert rep.ok  # not a hard issue
    assert any("split" in m or "%" in m for m in rep.warnings)


def test_missing_columns_is_an_issue():
    rep = check_bars(_clean().drop(columns=["volume"]))
    assert any("missing columns" in m for m in rep.issues)
