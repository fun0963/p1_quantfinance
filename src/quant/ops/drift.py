"""Backtest-vs-live drift — is the live system doing what the backtest said?

(breakdown M11.2) A strategy is validated in a backtest, then set loose live. Over
time the two can diverge: the live runner misses an entry (stale data, a crash, a
blocked order) or acts when the backtest wouldn't (a bug, a manual trade). Left
unmonitored, that gap silently invalidates every assumption the backtest made.

This module recomputes the strategy's *expected* entry/exit bars over a window and
compares them, by date, to the buy/sell decisions the live runner actually recorded
in the journal. It reports an agreement rate plus the specific divergences:

  * **missed** — the backtest would have traded on a bar but the live log shows no
    matching action (the dangerous one: a trade the system should have made).
  * **extra**  — the live log shows an action on a bar the backtest wouldn't trade.

Recompute-on-demand: no separate "expected" state is persisted; we re-derive it from
the same signal logic, so drift can't itself drift. Read-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from quant.strategies.base import BaseStrategy


def expected_action_bars(strategy: BaseStrategy, data: pd.DataFrame) -> dict[date, str]:
    """The bars where the strategy's target position flips, as {date: 'buy'|'sell'}.

    Mirrors the live runner's target-state logic: entries -> long, exits -> flat,
    forward-filled; a change in the held state on a bar is a buy (0->1) or sell (1->0)."""
    if data.empty:
        return {}
    signals = strategy.generate_signals(data)
    entries = signals["entries"].astype(bool)
    exits = signals["exits"].astype(bool)
    marks = pd.Series(float("nan"), index=signals.index)
    marks[entries] = 1.0
    marks[exits] = 0.0                      # exit wins if a bar has both
    state = marks.ffill().fillna(0.0)
    changed = state.ne(state.shift(1)) & marks.notna()
    out: dict[date, str] = {}
    for ts, is_change in changed.items():
        if is_change:
            out[_as_date(ts)] = "buy" if state.loc[ts] == 1.0 else "sell"
    return out


def _filter_symbol_strategy(df: pd.DataFrame, symbol: str | None,
                            strategy: str | None) -> pd.DataFrame:
    if symbol and "symbol" in df.columns:
        df = df[df["symbol"] == symbol]
    if strategy and "strategy" in df.columns:
        df = df[df["strategy"] == strategy]
    return df


def live_action_bars(live_log: pd.DataFrame, *, symbol: str | None = None,
                     strategy: str | None = None) -> dict[date, str]:
    """The buy/sell orders the live runner actually PLACED, as {date: action}.

    Only rows with an `order_id` count: a decision that was risk-gate blocked or ran
    in dry-run has action='buy'/'sell' but no order_id and never reached the market —
    counting it as a real trade would hide the very "missed entry" drift exists to
    catch.
    """
    if live_log.empty:
        return {}
    df = _filter_symbol_strategy(live_log, symbol, strategy)
    df = df[df["action"].isin(["buy", "sell"])]
    if "order_id" in df.columns:
        df = df[df["order_id"].notna()]              # actually placed, not blocked/dry-run
    out: dict[date, str] = {}
    for _, r in df.iterrows():
        out[_as_date(r["bar_ts"])] = r["action"]     # one action per bar; last wins
    return out


def live_coverage(live_log: pd.DataFrame, *, symbol: str | None = None,
                  strategy: str | None = None) -> tuple[date, date] | tuple[None, None]:
    """The (first, last) bar dates the live runner was actually running for this
    symbol/strategy — every run leaves a row (even a 'hold'), so this is the window
    in which a backtest signal *could* have been acted on. (None, None) if never run.
    """
    if live_log.empty or "bar_ts" not in live_log.columns:
        return (None, None)
    df = _filter_symbol_strategy(live_log, symbol, strategy)
    dates = df["bar_ts"].dropna()
    if dates.empty:
        return (None, None)
    as_dates = [_as_date(d) for d in dates]
    return (min(as_dates), max(as_dates))


@dataclass
class DriftReport:
    symbol: str | None
    strategy: str | None
    n_bars: int
    n_expected: int
    n_live: int
    n_matched: int
    agreement: float
    missed: list[tuple[date, str]] = field(default_factory=list)   # backtest traded, live didn't
    extra: list[tuple[date, str]] = field(default_factory=list)    # live traded, backtest wouldn't
    min_agreement: float = 0.8

    @property
    def ok(self) -> bool:
        # No expected signals AND no stray live actions = trivially in agreement.
        if self.n_expected == 0 and self.n_live == 0:
            return True
        return self.agreement >= self.min_agreement and not self.missed

    def summary(self) -> str:
        head = "drift: OK" if self.ok else "drift: DIVERGENCE"
        return (f"{head} - {self.strategy or '?'} on {self.symbol or '?'}: "
                f"agreement {self.agreement:.0%} "
                f"({self.n_matched}/{self.n_expected} expected matched, "
                f"{len(self.missed)} missed, {len(self.extra)} extra)")


def decision_drift(
    data: pd.DataFrame, strategy: BaseStrategy, live_log: pd.DataFrame, *,
    symbol: str | None = None, strategy_name: str | None = None,
    min_agreement: float = 0.8,
) -> DriftReport:
    """Compare backtest-expected trade bars to the live runner's placed orders.

    Only the window the live runner was actually running is compared — backtest
    signals from before deployment (or after the last run) are excluded so they don't
    show up as false "missed" divergences. If the runner never ran for this
    symbol/strategy there is nothing to compare, so the report is trivially OK.
    """
    expected = expected_action_bars(strategy, data)
    live = live_action_bars(live_log, symbol=symbol, strategy=strategy_name)

    lo, hi = live_coverage(live_log, symbol=symbol, strategy=strategy_name)
    if lo is None or hi is None:
        expected = {}                                 # runner never ran -> nothing to compare
    else:
        expected = {d: a for d, a in expected.items() if lo <= d <= hi}

    matched = [d for d, a in expected.items() if live.get(d) == a]
    missed = sorted((d, a) for d, a in expected.items() if live.get(d) != a)
    extra = sorted((d, a) for d, a in live.items() if expected.get(d) != a)

    denom = len(matched) + len(missed) + len(extra)
    agreement = 1.0 if denom == 0 else len(matched) / denom
    return DriftReport(
        symbol=symbol, strategy=strategy_name, n_bars=len(data),
        n_expected=len(expected), n_live=len(live), n_matched=len(matched),
        agreement=agreement, missed=missed, extra=extra, min_agreement=min_agreement,
    )


def _as_date(ts) -> date:
    """Normalise a timestamp/date/ISO-string to a plain date for alignment."""
    return pd.Timestamp(ts).date()
