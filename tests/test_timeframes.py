"""The canonical timeframe registry - single source of truth (Batch 4)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from quant.backtest.metrics import compute_metrics
from quant.data.feeds import get_feed
from quant.data.feeds.yfinance_feed import YFinanceFeed
from quant.data.timeframes import TIMEFRAMES, get_timeframe


def test_registry_covers_the_canonical_set():
    assert {"1min", "1h", "1d", "1wk", "1mo"} == set(TIMEFRAMES)
    assert get_timeframe("1min").periods_per_year == 252 * 390
    assert get_timeframe("1min").intraday and not get_timeframe("1d").intraday
    assert get_timeframe("1d").bar_seconds == 86_400


def test_unknown_timeframe_fails_loud():
    with pytest.raises(ValueError, match="unsupported timeframe"):
        get_timeframe("5m")


def test_vbt_freq_values_are_timedelta_parseable():
    for tf in TIMEFRAMES.values():
        pd.Timedelta(tf.vbt_freq)                  # must not raise


def test_default_feed_routing():
    assert isinstance(get_feed("1d"), YFinanceFeed)
    assert get_timeframe("1min").default_feed == "alpaca"   # AlpacaFeed needs keys;
    # routing is asserted via the registry, construction is covered elsewhere.


def test_minute_sharpe_annualizes_with_minute_periods():
    """Regression: the old metrics table had no '1min' and silently used 252,
    annualizing minute Sharpe as if bars were days (~20x understated)."""
    rng = np.random.default_rng(3)
    idx = pd.date_range("2026-01-05 09:30", periods=390 * 5, freq="1min", tz="UTC")
    eq = pd.Series(100_000 * np.exp(np.cumsum(rng.normal(2e-6, 2e-4, len(idx)))), index=idx)

    m_1min = compute_metrics(eq, timeframe="1min")
    m_wrong = compute_metrics(eq, timeframe="1d")   # same curve, daily annualization
    assert m_1min["sharpe"] is not None
    # sqrt(98280 / 252) ~ 19.7x scale difference between the two annualizations
    assert abs(m_1min["sharpe"]) > abs(m_wrong["sharpe"]) * 10


def test_compute_metrics_rejects_unknown_timeframe():
    idx = pd.date_range("2024-01-01", periods=50, freq="B", tz="UTC")
    eq = pd.Series(np.linspace(100, 110, 50), index=idx)
    with pytest.raises(ValueError, match="unsupported timeframe"):
        compute_metrics(eq, timeframe="bogus")
