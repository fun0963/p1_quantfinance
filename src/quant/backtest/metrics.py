"""Engine-agnostic performance metrics.

Both backtest engines produce an `equity_curve`; computing the headline metrics
here (rather than trusting each engine's native stats) guarantees an
apples-to-apples comparison between VectorBT and Backtrader.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from quant.data.timeframes import get_timeframe


def _psr_pct(rets: pd.Series) -> float | None:
    """Probabilistic Sharpe Ratio (Bailey & Lopez de Prado): probability that
    the TRUE Sharpe exceeds 0, given the sample length and the non-normality
    (skew/kurtosis) of returns. The honesty metric for short samples - a lucky
    six-month Sharpe 2 scores far lower than a five-year one. Uses the
    per-period Sharpe (annualization cancels out of the probability)."""
    n = len(rets)
    if n < 4:                                   # kurtosis needs 4 observations
        return None
    std = float(rets.std())
    if not std or std <= 0 or math.isnan(std):
        return None
    sr = float(rets.mean()) / std
    skew = float(rets.skew())
    kurt = float(rets.kurt()) + 3.0             # pandas reports EXCESS kurtosis
    if math.isnan(skew) or math.isnan(kurt):
        return None
    var_term = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr
    if var_term <= 0:                           # pathological higher moments
        return None
    z = sr * math.sqrt(n - 1) / math.sqrt(var_term)
    return round(0.5 * (1.0 + math.erf(z / math.sqrt(2.0))) * 100.0, 1)


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

    # Registry lookup, loud on unknown - the old private table silently fell
    # back to 252, which annualized 1min Sharpe as if bars were days.
    ppy = get_timeframe(timeframe).periods_per_year
    rets = eq.pct_change(fill_method=None).dropna()

    total_return = eq.iloc[-1] / eq.iloc[0] - 1.0
    n_periods = len(eq) - 1
    cagr = (eq.iloc[-1] / eq.iloc[0]) ** (ppy / n_periods) - 1.0 if n_periods > 0 else np.nan

    std = rets.std()
    sharpe = (rets.mean() / std) * np.sqrt(ppy) if std and std > 0 else np.nan

    # Sortino: like Sharpe but only downside deviation is "risk" (target return 0),
    # so it doesn't penalize a strategy for large upside moves.
    downside = np.minimum(rets.to_numpy(), 0.0)
    dd_dev = float(np.sqrt(np.mean(downside ** 2))) if len(downside) else np.nan
    sortino = (rets.mean() / dd_dev) * np.sqrt(ppy) if dd_dev and dd_dev > 0 else np.nan

    drawdown = eq / eq.cummax() - 1.0
    max_dd = drawdown.min()

    # Calmar: CAGR per unit of worst drawdown — return relative to pain endured.
    calmar = (cagr / abs(max_dd)) if (max_dd < 0 and not np.isnan(cagr)) else np.nan

    return {
        "total_return_pct": round(total_return * 100, 2),
        "cagr_pct": round(cagr * 100, 2) if not np.isnan(cagr) else None,
        "sharpe": round(sharpe, 2) if not np.isnan(sharpe) else None,
        "psr_pct": _psr_pct(rets),
        "sortino": round(sortino, 2) if not np.isnan(sortino) else None,
        "calmar": round(calmar, 2) if not np.isnan(calmar) else None,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "num_trades": num_trades,
        "final_equity": round(float(eq.iloc[-1]), 2),
    }


def turnover_annual(trades: pd.DataFrame | None, equity_curve: pd.Series,
                    timeframe: str = "1d") -> float | None:
    """Gross traded notional per year as a multiple of average equity.

    The cost-budget link: annual cost drag (bps) ~= turnover x per-side cost
    (bps), because every side pays costs on its own notional. Needs size and
    price columns (vectorbt's records_readable); returns None when they are
    absent (backtrader's trade log carries no size) or the window degenerates.
    Open trades count their entry side only (exit price is NaN).
    """
    if trades is None or len(trades) == 0:
        return None
    size = trades.get("Size")
    entry_px = trades.get("Avg Entry Price")
    exit_px = trades.get("Avg Exit Price")
    if size is None or entry_px is None or exit_px is None:
        return None
    notional = float((size.abs() * entry_px.abs()).sum()
                     + (size.abs() * exit_px.fillna(0.0).abs()).sum())
    eq = equity_curve.dropna().astype(float)
    if len(eq) < 2 or eq.mean() <= 0:
        return None
    years = (len(eq) - 1) / get_timeframe(timeframe).periods_per_year
    if years <= 0:
        return None
    return float(notional / eq.mean() / years)


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


def monthly_returns(equity_curve: pd.Series) -> pd.DataFrame:
    """Per-month return % as a year x month (1-12) table — the shape a monthly
    returns heatmap wants. The first month is measured from inception equity;
    each later month from the previous month-end. Empty frame if too short."""
    eq = equity_curve.dropna().astype(float)
    if len(eq) < 2:
        return pd.DataFrame()
    if not isinstance(eq.index, pd.DatetimeIndex):
        eq.index = pd.DatetimeIndex(eq.index)

    month_end = eq.resample("ME").last()
    prev = month_end.shift(1)
    prev.iloc[0] = eq.iloc[0]                      # first month vs inception, not NaN
    rets = (month_end / prev - 1.0) * 100.0

    table: dict[int, dict[int, float]] = {}
    for ts, r in rets.items():
        if pd.notna(r):
            table.setdefault(ts.year, {})[ts.month] = round(float(r), 2)
    df = pd.DataFrame.from_dict(table, orient="index").reindex(columns=range(1, 13))
    df.index.name = "year"
    return df.sort_index()
