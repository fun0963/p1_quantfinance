"""Engine-agnostic performance metrics.

Both backtest engines produce an `equity_curve`; computing the headline metrics
here (rather than trusting each engine's native stats) guarantees an
apples-to-apples comparison between VectorBT and Backtrader.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# Trading periods per year, keyed by canonical timeframe (for annualization).
_PERIODS_PER_YEAR = {"1d": 252, "1h": 252 * 6.5, "1wk": 52, "1mo": 12}


def compute_metrics(
    equity_curve: pd.Series,
    num_trades: int | None = None,
    timeframe: str = "1d",
) -> dict:
    """Headline metrics from an equity curve.

    Returns total return, CAGR, annualized Sharpe (rf=0), and max drawdown — all
    as percentages where applicable, rounded for display.
    """
    eq = equity_curve.dropna().astype(float)
    if len(eq) < 2:
        return {"error": "equity curve too short"}

    ppy = _PERIODS_PER_YEAR.get(timeframe, 252)
    rets = eq.pct_change(fill_method=None).dropna()

    total_return = eq.iloc[-1] / eq.iloc[0] - 1.0
    n_periods = len(eq) - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (ppy / n_periods) - 1.0 if n_periods > 0 else np.nan

    std = rets.std()
    sharpe = (rets.mean() / std) * np.sqrt(ppy) if std and std > 0 else np.nan

    drawdown = eq / eq.cummax() - 1.0
    max_dd = drawdown.min()

    return {
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2) if not np.isnan(cagr) else None,
        "sharpe": round(sharpe, 2) if not np.isnan(sharpe) else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "num_trades": num_trades,
        "final_equity": round(float(eq.iloc[-1]), 2),
    }


def trade_stats(trades: pd.DataFrame | None, pnl_col: str = "PnL") -> dict:
    """Per-trade quality stats from a trades table (e.g. VectorBT's
    `trades.records_readable`, which has a 'PnL' column).

    Returns win rate, payoff ratio (avg win / avg loss) and profit factor
    (gross profit / gross loss) — the "does it win often, or win big?" view.
    Empty dict when there are no trades or no PnL column.
    """
    if trades is None or len(trades) == 0 or pnl_col not in trades:
        return {}
    pnl = trades[pnl_col].astype(float)
    wins, losses = pnl[pnl > 0], pnl[pnl < 0]
    n = len(pnl)
    avg_win = float(wins.mean()) if len(wins) else 0.0
    avg_loss = abs(float(losses.mean())) if len(losses) else 0.0
    gross_loss = abs(float(losses.sum()))
    return {
        "win_rate_pct": round(len(wins) / n * 100, 2) if n else None,
        "payoff_ratio": round(avg_win / avg_loss, 2) if avg_loss > 0 else None,
        "profit_factor": round(float(wins.sum()) / gross_loss, 2) if gross_loss > 0 else None,
        "num_wins": int(len(wins)),
        "num_losses": int(len(losses)),
    }


def alpha_beta(strat_returns: pd.Series, bench_returns: pd.Series,
               periods_per_year: int = 252) -> dict:
    """CAPM alpha (annualized %) and beta of a strategy vs a benchmark, from
    aligned daily-return series. Beta = market exposure; alpha = excess over it.
    Empty dict if the series don't overlap or the benchmark has no variance.
    """
    df = pd.concat([strat_returns.rename("s"), bench_returns.rename("b")], axis=1).dropna()
    if len(df) < 2:
        return {}
    var_b = float(df["b"].var())
    if var_b <= 0:
        return {}
    beta = float(df["s"].cov(df["b"]) / var_b)
    alpha_daily = float(df["s"].mean() - beta * df["b"].mean())
    return {"alpha_pct": round(alpha_daily * periods_per_year * 100, 2), "beta": round(beta, 2)}


def yearly_returns(equity_curve: pd.Series) -> dict[int, float]:
    """Per-calendar-year return % from an equity curve (each year: last/first - 1)."""
    eq = equity_curve.dropna().astype(float)
    years = pd.DatetimeIndex(eq.index).year
    return {int(y): round((g.iloc[-1] / g.iloc[0] - 1) * 100, 2)
            for y, g in eq.groupby(years)}
