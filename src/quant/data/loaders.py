"""Convenience loaders that combine a feed with the parquet cache.

`load_bars` is the one-call way the CLI / notebooks get price data: serve from
the local parquet store if present, otherwise download once and cache it.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from quant.data.feeds.base import DataFeed
from quant.data.storage import get_store
from quant.utils import get_logger

log = get_logger(__name__)

# A cache whose earliest bar is at most this many days after the requested start
# still "covers" it: the gap is just non-trading days (weekend/holiday) or the
# symbol wasn't listed yet — re-downloading wouldn't recover earlier history.
_CACHE_START_TOLERANCE_DAYS = 7


def _cache_covers(first_bar: date, start: date) -> bool:
    """Whether a cache starting at `first_bar` covers a request from `start`."""
    return (first_bar - start).days <= _CACHE_START_TOLERANCE_DAYS


def load_bars(
    symbol: str,
    feed: DataFeed,
    start: datetime | None = None,
    timeframe: str = "1d",
    use_cache: bool = True,
) -> pd.DataFrame:
    """Return OHLCV bars from `start` onward, served from cache when it covers the range."""
    store = get_store()
    start = start or datetime(2020, 1, 1, tzinfo=UTC)

    if use_cache and store.exists(symbol, timeframe):
        cached = store.load(symbol, timeframe)
        if cached is not None and not cached.empty:
            # Compare on calendar date to sidestep tz-aware/naive index mismatches.
            if _cache_covers(cached.index[0].date(), start.date()):
                log.debug(f"cache hit {symbol} {timeframe} (first bar {cached.index[0].date()}, "
                          f"requested {start.date()})")
                return _slice_from(cached, start)  # honor the requested start
            gap = (cached.index[0].date() - start.date()).days
            log.info(f"cache for {symbol} starts {cached.index[0].date()}, {gap}d after "
                     f"requested {start.date()} — re-downloading for earlier history")

    df = feed.get_history(symbol, start=start, timeframe=timeframe)
    store.save(symbol, timeframe, df)  # cache the full pull; callers get the sliced view
    log.info(f"downloaded & cached {len(df)} bars for {symbol}")

    # Surface data-quality problems early — they silently corrupt backtests.
    from quant.data.quality import check_bars

    report = check_bars(df)
    for msg in report.issues:
        log.warning(f"data quality [{symbol}] ISSUE: {msg}")
    for msg in report.warnings:
        log.info(f"data quality [{symbol}] warn: {msg}")
    return _slice_from(df, start)


def _slice_from(df: pd.DataFrame, start: datetime) -> pd.DataFrame:
    """Return rows on/after `start`, tolerating tz-aware/naive index mismatches."""
    sd = pd.Timestamp(start)
    if df.index.tz is not None and sd.tz is None:
        sd = sd.tz_localize(df.index.tz)
    elif df.index.tz is None and sd.tz is not None:
        sd = sd.tz_localize(None)
    return df[df.index >= sd]
