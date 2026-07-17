"""Vectorized parameter sweeps — VectorBT's core strength, strategy-agnostic.

`sweep` evaluates a whole parameter grid for ANY strategy in a *single*
`Portfolio.from_signals` call (columns = parameter combos), then ranks them by a
consistent metric (`compute_metrics`, so results match `quant backtest`). This is
the research-stage funnel: scan hundreds of combos fast, hand the survivors to
the Backtrader engine — and to walk-forward — for honest confirmation.
"""
from __future__ import annotations

from itertools import product

import pandas as pd

from quant.backtest.metrics import compute_metrics
from quant.strategies.base import BaseStrategy
from quant.utils import get_logger

log = get_logger(__name__)


def _combo_label(params: dict) -> str:
    """Stable, unique column label for a parameter combo."""
    return "__".join(f"{k}{v}" for k, v in params.items())


def expand_grid(strategy_cls: type[BaseStrategy], grid: dict[str, list]) -> list[dict]:
    """Cartesian product of `grid`, filtered by the strategy's validity rule."""
    names = list(grid)
    combos = [dict(zip(names, vals)) for vals in product(*grid.values())]
    valid = [c for c in combos if strategy_cls.params_valid(**c)]
    if not valid:
        raise ValueError("no valid parameter combos for this grid")
    return valid


def sweep(
    strategy_cls: type[BaseStrategy],
    data: pd.DataFrame,
    grid: dict[str, list] | None = None,
    cash: float = 100_000,
    fees: float = 0.0005,
    sort_by: str = "sharpe",
    timeframe: str = "1d",
    slippage: float = 0.0,
) -> pd.DataFrame:
    """Backtest every valid param combo at once; return a ranked table.

    Columns: <strategy params...>, total_return_pct, sharpe, max_drawdown_pct,
    num_trades. Sorted best-first by `sort_by`.
    """
    import vectorbt as vbt

    grid = grid or strategy_cls.default_grid()
    if not grid:
        raise ValueError(f"{strategy_cls.__name__} has no default_grid; pass one explicitly")

    combos = expand_grid(strategy_cls, grid)
    cols, entries, exits = [], {}, {}
    for params in combos:
        col = _combo_label(params)
        sig = strategy_cls(**params).generate_signals(data)
        cols.append(col)
        entries[col] = sig["entries"]
        exits[col] = sig["exits"]

    entries_df = pd.DataFrame(entries)[cols]
    exits_df = pd.DataFrame(exits)[cols]
    log.info(f"sweeping {len(combos)} {strategy_cls.name} combos in one vectorbt pass "
             f"({len(data)} bars)")

    from quant.data.timeframes import get_timeframe

    pf = vbt.Portfolio.from_signals(
        close=data["close"],          # 1D close broadcasts across all columns
        entries=entries_df,
        exits=exits_df,
        init_cash=cash,
        fees=fees,
        slippage=slippage,
        freq=get_timeframe(timeframe).vbt_freq,
    )

    # compute_metrics per column → identical metric definitions to `quant backtest`.
    equity = pf.value()
    trade_counts = pf.trades.count().reindex(cols)
    rows = []
    for params, col in zip(combos, cols):
        m = compute_metrics(equity[col], num_trades=int(trade_counts[col]), timeframe=timeframe)
        rows.append({
            **params,
            "total_return_pct": m["total_return_pct"],
            "sharpe": m["sharpe"],
            "max_drawdown_pct": m["max_drawdown_pct"],
            "num_trades": m["num_trades"],
        })
    results = pd.DataFrame(rows, index=cols)

    metric_cols = {"total_return_pct", "sharpe", "max_drawdown_pct", "num_trades"}
    if sort_by not in metric_cols:
        raise ValueError(f"sort_by must be one of {sorted(metric_cols)}")
    ascending = sort_by == "max_drawdown_pct"  # less-negative drawdown is better
    return results.sort_values(sort_by, ascending=ascending)


