"""Data-quality checks — guard the integrity of every backtest.

Backtest results are only as good as the bars they run on. `check_bars` surfaces
the silent killers: NaNs, duplicate or unsorted timestamps, non-positive prices,
broken OHLC relationships, and suspicious calendar gaps (often unadjusted splits).
It reports rather than mutates — the caller decides whether to clean or abort.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from quant.data.feeds.base import DataFeed


@dataclass
class QualityReport:
    n_bars: int
    issues: list[str] = field(default_factory=list)   # blocking problems
    warnings: list[str] = field(default_factory=list)  # worth a look, not fatal

    @property
    def ok(self) -> bool:
        return not self.issues

    def __str__(self) -> str:
        lines = [f"QualityReport: {self.n_bars} bars, "
                 f"{'OK' if self.ok else f'{len(self.issues)} issue(s)'}"]
        lines += [f"  [ISSUE] {m}" for m in self.issues]
        lines += [f"  [warn ] {m}" for m in self.warnings]
        return "\n".join(lines)


def check_bars(
    df: pd.DataFrame,
    max_gap_days: float = 5.0,
    max_jump: float = 0.5,
) -> QualityReport:
    """Validate an OHLCV frame; return a structured report.

    Args:
        max_gap_days: flag spacing larger than this between consecutive bars.
        max_jump: flag |close-to-close return| above this (e.g. 0.5 = 50%),
                  a common fingerprint of an unadjusted split/dividend.
    """
    rep = QualityReport(n_bars=len(df))

    if df.empty:
        rep.issues.append("frame is empty")
        return rep

    missing = set(DataFeed.COLUMNS) - set(df.columns)
    if missing:
        rep.issues.append(f"missing columns: {sorted(missing)}")
        return rep  # nothing else is meaningful without the columns

    # --- index integrity ---
    if not isinstance(df.index, pd.DatetimeIndex):
        rep.issues.append("index is not a DatetimeIndex")
    else:
        if not df.index.is_monotonic_increasing:
            rep.issues.append("index is not sorted ascending")
        n_dupes = int(df.index.duplicated().sum())
        if n_dupes:
            rep.issues.append(f"{n_dupes} duplicate timestamp(s)")

    # --- NaNs ---
    nan_counts = df[DataFeed.COLUMNS].isna().sum()
    nan_cols = nan_counts[nan_counts > 0]
    if not nan_cols.empty:
        rep.issues.append(f"NaNs present: {nan_cols.to_dict()}")

    # --- price sanity ---
    price_cols = ["open", "high", "low", "close"]
    if (df[price_cols] <= 0).any().any():
        rep.issues.append("non-positive prices found")
    # OHLC relationships: high must dominate, low must be the floor.
    bad_hl = (df["high"] < df["low"]).sum()
    bad_hi = ((df["high"] < df["open"]) | (df["high"] < df["close"])).sum()
    bad_lo = ((df["low"] > df["open"]) | (df["low"] > df["close"])).sum()
    if bad_hl or bad_hi or bad_lo:
        rep.issues.append(
            f"OHLC inconsistencies: high<low={int(bad_hl)}, "
            f"high<o/c={int(bad_hi)}, low>o/c={int(bad_lo)}"
        )
    if (df["volume"] < 0).any():
        rep.issues.append("negative volume found")

    # --- gaps (warning: calendars legitimately skip weekends/holidays) ---
    if isinstance(df.index, pd.DatetimeIndex) and len(df) > 1:
        deltas = df.index.to_series().diff().dropna()
        big = deltas[deltas > pd.Timedelta(days=max_gap_days)]
        if not big.empty:
            worst = big.max()
            rep.warnings.append(
                f"{len(big)} gap(s) > {max_gap_days}d (largest {worst.days}d) — "
                "check for missing data or halts"
            )

    # --- price jumps (warning: often unadjusted splits) ---
    rets = df["close"].pct_change(fill_method=None).abs()
    jumps = rets[rets > max_jump]
    if not jumps.empty:
        rep.warnings.append(
            f"{len(jumps)} close-to-close move(s) > {max_jump:.0%} "
            f"(max {jumps.max():.0%}) — possible unadjusted split/error"
        )

    return rep
