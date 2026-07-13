"""Strategy lifecycle checks — pre-committed promote/retire discipline (M6.5).

The retirement rule you write BEFORE deploying is the one you'll actually obey;
the one you improvise during a drawdown is a negotiation with yourself. Rules
(rolling-window Sharpe floor, drawdown floor, minimum activity) live in the
versioned strategy spec; this module only *evaluates* them against a recent
equity window and reports — the standard read-only analysis-object pattern
(like ReconcileReport / HealthReport), so the caller decides what to do.

Depends only on the pure metric functions (no engine import): the caller runs
the backtest and hands in the equity curve.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant.backtest.metrics import compute_metrics


@dataclass(frozen=True)
class LifecycleRules:
    """Pre-committed health thresholds, evaluated on the trailing window."""
    eval_bars: int = 252                 # trailing window length (~1 trading year)
    min_rolling_sharpe: float = 0.0      # window Sharpe must stay at/above this
    max_drawdown_pct: float = -25.0      # window drawdown must stay above (less negative)
    min_trades: int = 1                  # entries in window: a dead strategy is also unhealthy

    @classmethod
    def from_dict(cls, d: dict) -> LifecycleRules:
        """Build from a spec's `lifecycle` block, ignoring non-rule keys (state)."""
        keys = {"eval_bars", "min_rolling_sharpe", "max_drawdown_pct", "min_trades"}
        return cls(**{k: v for k, v in d.items() if k in keys})


@dataclass
class LifecycleReport:
    """Read-only verdict for one strategy spec on its trailing window."""
    name: str
    state: str                          # spec's recorded state (research/paper/live/retired)
    window_bars: int
    rolling_sharpe: float | None
    window_return_pct: float | None
    window_drawdown_pct: float | None
    trades: int
    rules: LifecycleRules
    breaches: list[str]

    @property
    def ok(self) -> bool:
        return not self.breaches

    @property
    def verdict(self) -> str:
        """'hold' = keep current state; 'retire-review' = a pre-committed rule broke."""
        return "hold" if self.ok else "retire-review"

    def summary(self) -> str:
        rs = "n/a" if self.rolling_sharpe is None else f"{self.rolling_sharpe:.2f}"
        dd = "n/a" if self.window_drawdown_pct is None else f"{self.window_drawdown_pct:.1f}%"
        return (f"lifecycle [{self.name}] state={self.state} "
                f"window={self.window_bars} bars: sharpe {rs} "
                f"(min {self.rules.min_rolling_sharpe}), dd {dd} "
                f"(floor {self.rules.max_drawdown_pct}%), trades {self.trades} "
                f"(min {self.rules.min_trades}) -> {self.verdict.upper()}")


def check_lifecycle(
    name: str,
    *,
    state: str,
    equity: pd.Series,
    num_trades: int,
    rules: LifecycleRules,
    timeframe: str = "1d",
) -> LifecycleReport:
    """Evaluate the pre-committed rules on the trailing `rules.eval_bars` of `equity`.

    `num_trades` is the entry count within that window (the caller derives it from
    the strategy's signals). Too-short history is itself a breach — a rule that
    silently passes on missing data would defeat the discipline.
    """
    window = equity.dropna().iloc[-rules.eval_bars:]
    breaches: list[str] = []

    m = compute_metrics(window, num_trades=num_trades, timeframe=timeframe)
    if "error" in m:
        return LifecycleReport(name=name, state=state, window_bars=len(window),
                               rolling_sharpe=None, window_return_pct=None,
                               window_drawdown_pct=None, trades=num_trades, rules=rules,
                               breaches=[f"insufficient history: {m['error']}"])

    sharpe = m["sharpe"]
    dd = m["max_drawdown_pct"]
    if sharpe is None:
        breaches.append("window sharpe undefined (flat equity)")
    elif sharpe < rules.min_rolling_sharpe:
        breaches.append(f"rolling sharpe {sharpe:.2f} below floor {rules.min_rolling_sharpe}")
    if dd < rules.max_drawdown_pct:
        breaches.append(f"window drawdown {dd:.1f}% below floor {rules.max_drawdown_pct}%")
    if num_trades < rules.min_trades:
        breaches.append(f"only {num_trades} trade(s) in window, min {rules.min_trades}")

    return LifecycleReport(name=name, state=state, window_bars=len(window),
                           rolling_sharpe=sharpe, window_return_pct=m["total_return_pct"],
                           window_drawdown_pct=dd, trades=num_trades, rules=rules,
                           breaches=breaches)
