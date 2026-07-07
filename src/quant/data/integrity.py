"""Point-in-time data integrity — catch silent rewrites of settled history.

The quiet killer of reproducible research: `yfinance(auto_adjust=True)` returns
split/dividend-adjusted prices, so when a corporate action happens EVERY past bar is
rewritten. Because `load_bars` overwrites the parquet cache on any re-download, a
backtest run today can see different "historical" prices than the same backtest run
last month — with no warning. That is a point-in-time violation: the past changed.

`detect_history_mutation` compares a freshly downloaded frame against the cached one
over their *settled* overlap and reports whether the past was rewritten (and by how
much — a whole-series ratio shift is the fingerprint of a split/adjustment). The
loader calls it before overwriting the cache so the event is at least logged and
recorded, and `quant integrity` surfaces it. Read-only: it reports, never mutates.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd

from config import get_settings

_EVENTS_FILE = "integrity_events.csv"
_EVENT_COLUMNS = ["detected_at", "symbol", "timeframe", "n_changed",
                  "n_overlap", "max_rel_change", "first_changed"]


@dataclass
class MutationReport:
    symbol: str
    timeframe: str
    n_overlap: int                       # settled bars compared (present in both)
    n_changed: int                       # bars whose price changed beyond tolerance
    max_rel_change: float                # largest relative close change over the overlap
    first_changed: date | None
    samples: list[tuple[date, float, float]] = field(default_factory=list)  # (date, old, new)

    @property
    def mutated(self) -> bool:
        return self.n_changed > 0

    def summary(self) -> str:
        if self.n_overlap == 0:
            return f"{self.symbol} {self.timeframe}: no settled overlap to compare"
        if not self.mutated:
            return (f"{self.symbol} {self.timeframe}: history unchanged "
                    f"over {self.n_overlap} overlapping bar(s)")
        # A rewrite of most of the series looks like a split/adjustment; a few bars
        # looks like a targeted revision or a late data correction.
        kind = ("likely split/adjustment" if self.n_changed > self.n_overlap * 0.5
                else "partial revision")
        return (f"{self.symbol} {self.timeframe}: {self.n_changed}/{self.n_overlap} settled "
                f"bar(s) REWRITTEN (max {self.max_rel_change:.1%} change, "
                f"first {self.first_changed}) - {kind}")


def _by_date(df: pd.DataFrame) -> pd.DataFrame:
    """Reindex a bar frame by tz-naive calendar date (last row wins on any dupe), so
    two frames align regardless of tz-aware/naive index differences."""
    out = df.sort_index().copy()
    idx = pd.DatetimeIndex(out.index)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    out.index = idx.normalize()
    return out[~out.index.duplicated(keep="last")]


def detect_history_mutation(
    old: pd.DataFrame | None, new: pd.DataFrame | None, *,
    symbol: str = "", timeframe: str = "1d", rtol: float = 1e-3, exclude_last: int = 1,
) -> MutationReport:
    """Compare `new` against cached `old` over their settled overlap.

    `exclude_last` drops the newest bar(s) of `old` from the comparison — a cache's
    last bar can be a still-forming session that legitimately finalises to a slightly
    different value; that is not a rewrite of *settled* history. `rtol` is the relative
    tolerance below which a bar counts as unchanged (absorbs float noise).
    """
    empty = MutationReport(symbol, timeframe, 0, 0, 0.0, None)
    if old is None or old.empty or new is None or new.empty:
        return empty
    if "close" not in old.columns or "close" not in new.columns:
        return empty

    o = _by_date(old)
    if exclude_last > 0 and len(o) > exclude_last:
        o = o.iloc[:-exclude_last]              # ignore possibly-provisional newest bar(s)
    n = _by_date(new)

    common = o.index.intersection(n.index)
    if len(common) == 0:
        return empty

    old_close = o.loc[common, "close"].astype(float)
    new_close = n.loc[common, "close"].astype(float)
    denom = old_close.abs().where(old_close.abs() > 0)
    rel = ((new_close - old_close).abs() / denom).dropna()
    if rel.empty:
        return MutationReport(symbol, timeframe, len(common), 0, 0.0, None)

    changed = rel[rel > rtol]
    if changed.empty:
        return MutationReport(symbol, timeframe, len(common), 0, float(rel.max()), None)

    changed_sorted = changed.sort_index()
    samples = [(ts.date(), float(old_close.loc[ts]), float(new_close.loc[ts]))
               for ts in changed_sorted.index[:5]]
    return MutationReport(
        symbol=symbol, timeframe=timeframe, n_overlap=len(common),
        n_changed=int(len(changed)), max_rel_change=float(rel.max()),
        first_changed=changed_sorted.index[0].date(), samples=samples,
    )


def record_mutation_event(report: MutationReport, *, data_dir: Path | None = None,
                          at: str | None = None) -> Path | None:
    """Append a mutated report to the integrity-events CSV (no-op if not mutated)."""
    if not report.mutated:
        return None
    d = data_dir or get_settings().data_dir
    d.mkdir(parents=True, exist_ok=True)
    path = d / _EVENTS_FILE
    at = at or datetime.now(UTC).isoformat(timespec="seconds")
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(_EVENT_COLUMNS)
        w.writerow([at, report.symbol, report.timeframe, report.n_changed,
                    report.n_overlap, f"{report.max_rel_change:.6f}",
                    report.first_changed.isoformat() if report.first_changed else ""])
    return path


def read_mutation_events(data_dir: Path | None = None) -> pd.DataFrame:
    """Recorded history-mutation events (newest first), or an empty frame."""
    d = data_dir or get_settings().data_dir
    path = d / _EVENTS_FILE
    if not path.exists():
        return pd.DataFrame(columns=_EVENT_COLUMNS)
    return pd.read_csv(path).iloc[::-1].reset_index(drop=True)
