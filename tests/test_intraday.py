"""Intraday enablement (Batch 4): bar-unit freshness gates, the interval
scheduler's parsing + market-hours gate, and the live step's seconds-based
staleness check. Offline: no network, brokers are the in-memory paper sim."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from quant.data.loaders import _cache_fresh_bars
from quant.execution.live_runner import run_live_step
from quant.execution.paper_broker import PaperBroker
from quant.execution.scheduler import _is_market_open, _parse_every
from quant.strategies.momentum import MomentumStrategy


def _minute_frame(n=120, end=None):
    end = end or pd.Timestamp.now(tz=UTC).floor("min")
    idx = pd.date_range(end=end, periods=n, freq="1min", tz="UTC")
    close = pd.Series(100 + np.arange(n) * 0.01, index=idx)   # steady uptrend
    return pd.DataFrame({"open": close, "high": close * 1.0001, "low": close * 0.9999,
                         "close": close, "volume": 1e4}, index=idx)


# --- loaders: bar-unit cache freshness ---------------------------------------
def test_cache_fresh_bars_within_and_beyond_tolerance():
    now = pd.Timestamp.now(tz=UTC)
    assert _cache_fresh_bars(now - timedelta(seconds=90), "1min", 2)        # 1.5 bars old
    assert not _cache_fresh_bars(now - timedelta(seconds=200), "1min", 2)   # >3 bars old
    assert _cache_fresh_bars(now - timedelta(minutes=90), "1h", 2)          # 1.5 hourly bars


def test_cache_fresh_bars_handles_naive_timestamps():
    naive = pd.Timestamp.now(tz=UTC).tz_localize(None) - timedelta(seconds=30)
    assert _cache_fresh_bars(naive, "1min", 1)


# --- live step: seconds-based bar-age gate -----------------------------------
def test_seconds_gate_blocks_stale_minute_bar():
    data = _minute_frame(end=pd.Timestamp.now(tz=UTC) - timedelta(minutes=30))
    dec = run_live_step(MomentumStrategy(lookback=20), data, "SPY", PaperBroker(),
                        max_bar_age_seconds=5 * 60)           # 5 bars x 1min
    assert dec.blocked and "stale data" in dec.blocked


def test_seconds_gate_allows_fresh_minute_bar_and_trades():
    data = _minute_frame()                                     # newest bar ~now
    dec = run_live_step(MomentumStrategy(lookback=20), data, "SPY", PaperBroker(),
                        dry_run=True, max_bar_age_seconds=5 * 60)
    assert not dec.blocked
    assert dec.target_state == "long"                          # uptrend wants long


def test_seconds_gate_takes_priority_over_day_gate():
    # 30 minutes old: fine by the day gate, stale by a 5-bar minute gate.
    data = _minute_frame(end=pd.Timestamp.now(tz=UTC) - timedelta(minutes=30))
    dec = run_live_step(MomentumStrategy(lookback=20), data, "SPY", PaperBroker(),
                        max_bar_age_days=4, max_bar_age_seconds=5 * 60)
    assert dec.blocked


# --- scheduler: interval parsing + market-hours gate -------------------------
def test_parse_every_accepts_minutes_and_hours():
    assert _parse_every("5min") == 5
    assert _parse_every("15min") == 15
    assert _parse_every("1h") == 60


def test_parse_every_rejects_garbage():
    with pytest.raises(ValueError, match="unsupported interval"):
        _parse_every("fast")


def test_market_open_gate_regular_session_vs_night():
    # 2026-07-15 was a regular Wednesday session: 18:00 UTC is mid-session,
    # 02:00 UTC is overnight; 2026-07-04 sat on Independence-Day weekend.
    assert _is_market_open(datetime(2026, 7, 15, 18, 0, tzinfo=UTC))
    assert not _is_market_open(datetime(2026, 7, 15, 2, 0, tzinfo=UTC))
    assert not _is_market_open(datetime(2026, 7, 4, 18, 0, tzinfo=UTC))
