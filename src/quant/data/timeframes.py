"""Canonical timeframe registry — the single source of truth (Batch 4).

Everything the system needs to know about a bar size lives here: how long a bar
is, how many fit in a trading year (metric annualization), which feed serves it
by default, and what to call it in pandas/vectorbt. Before this, that knowledge
was scattered (metrics had its own table with a silent 252 fallback, the feeds
each had a mapping, freshness gates assumed days) — and "1min" fell through
every crack.

Feed defaults: yfinance serves daily+ research keylessly; intraday goes to
Alpaca (yfinance 1min history is ~7 days — useless; Alpaca's free IEX feed is
the workable source).
"""
from __future__ import annotations

from dataclasses import dataclass

# ~252 trading days/year; a US equities regular session is 6.5h = 390 minutes.
_MINUTES_PER_SESSION = 390


@dataclass(frozen=True)
class Timeframe:
    name: str                 # canonical key used across the system
    bar_seconds: int          # wall-clock length of one bar (session time)
    periods_per_year: float   # bars per year, for annualizing Sharpe/CAGR
    vbt_freq: str             # pandas-timedelta-parseable freq for vectorbt
    default_feed: str         # "yfinance" | "alpaca"
    intraday: bool            # True -> freshness is measured in bars, not days

    @property
    def bars_per_day(self) -> float:
        return self.periods_per_year / 252


TIMEFRAMES: dict[str, Timeframe] = {
    "1min": Timeframe("1min", 60, 252 * _MINUTES_PER_SESSION, "1min", "alpaca", True),
    "1h":   Timeframe("1h", 3600, 252 * 6.5, "1h", "yfinance", True),
    "1d":   Timeframe("1d", 86_400, 252, "1D", "yfinance", False),
    "1wk":  Timeframe("1wk", 7 * 86_400, 52, "7D", "yfinance", False),
    "1mo":  Timeframe("1mo", 30 * 86_400, 12, "30D", "yfinance", False),
}


def get_timeframe(name: str) -> Timeframe:
    """Resolve a canonical timeframe or fail loud (same philosophy as the feed
    whitelist: an unknown timeframe must never silently become something else)."""
    if name not in TIMEFRAMES:
        raise ValueError(f"unsupported timeframe {name!r}; supported: {sorted(TIMEFRAMES)}")
    return TIMEFRAMES[name]
