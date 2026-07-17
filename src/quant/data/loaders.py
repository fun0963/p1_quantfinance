"""Convenience loaders that combine a feed with the parquet cache.

`load_bars` is the one-call way the CLI / notebooks get price data: serve from
the local parquet store if present, otherwise download once and cache it.
`fetch_bars` is the string-friendly wrapper the entrypoints (CLI, web, portfolio)
share, so the "ISO date + default feed" plumbing lives in exactly one place.
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


def _cache_fresh(last_bar: date, now: date, max_staleness_days: int) -> bool:
    """Whether a cache whose newest bar is `last_bar` is recent enough (used by the
    live path so it never trades on a stale cache — see max_staleness_days)."""
    return (now - last_bar).days <= max_staleness_days


def load_bars(
    symbol: str,
    feed: DataFeed,
    start: datetime | None = None,
    timeframe: str = "1d",
    use_cache: bool = True,
    max_staleness_days: int | None = None,
) -> pd.DataFrame:
    """Return OHLCV bars from `start` onward, served from cache when it covers the range.

    `max_staleness_days`: if set, a cache whose newest bar is older than this many
    days is re-downloaded rather than served — the live path passes a small value so
    it never decides on a stale cache. Leave None for research (cache is fine).
    """
    store = get_store()
    start = start or datetime(2020, 1, 1, tzinfo=UTC)
    prior: pd.DataFrame | None = None   # cache we're about to replace, for a point-in-time check

    if use_cache and store.exists(symbol, timeframe):
        cached = store.load(symbol, timeframe)
        if cached is not None and not cached.empty:
            prior = cached
            # Compare on calendar date to sidestep tz-aware/naive index mismatches.
            covers = _cache_covers(cached.index[0].date(), start.date())
            fresh = (max_staleness_days is None or
                     _cache_fresh(cached.index[-1].date(), datetime.now(UTC).date(),
                                  max_staleness_days))
            if covers and fresh:
                log.debug(f"cache hit {symbol} {timeframe} (first bar {cached.index[0].date()}, "
                          f"requested {start.date()})")
                return _slice_from(cached, start)  # honor the requested start
            if not covers:
                gap = (cached.index[0].date() - start.date()).days
                log.info(f"cache for {symbol} starts {cached.index[0].date()}, {gap}d after "
                         f"requested {start.date()} — re-downloading for earlier history")
            else:
                log.info(f"cache for {symbol} newest bar {cached.index[-1].date()} older than "
                         f"{max_staleness_days}d — re-downloading for freshness")

    df = feed.get_history(symbol, start=start, timeframe=timeframe)

    # Point-in-time guard: if this download rewrote SETTLED history vs the cache we're
    # about to overwrite (a split/adjustment), the past just changed — never silently.
    if prior is not None:
        from quant.data.integrity import detect_history_mutation, record_mutation_event

        mrep = detect_history_mutation(prior, df, symbol=symbol, timeframe=timeframe)
        if mrep.mutated:
            log.warning(f"data integrity [{symbol}]: {mrep.summary()}")
            record_mutation_event(mrep)

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


def fetch_bars(
    symbol: str,
    start: str = "2020-01-01",
    timeframe: str = "1d",
    use_cache: bool = True,
) -> pd.DataFrame:
    """`load_bars` with the entrypoint-friendly signature: ISO date string and the
    timeframe's default feed (yfinance for daily+, Alpaca for intraday). CLI /
    web / portfolio all funnel through here instead of each re-implementing the
    date parsing + feed construction."""
    from quant.data.feeds import get_feed

    start_dt = datetime.fromisoformat(start).replace(tzinfo=UTC)
    return load_bars(symbol, get_feed(timeframe), start=start_dt, timeframe=timeframe,
                     use_cache=use_cache)
