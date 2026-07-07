"""Portfolio-level allocation across multiple strategies.

A portfolio is a set of *legs* — each leg is (symbol, strategy, params, weight).
Capital is split by weight; each leg is backtested independently (reusing the
VectorBT engine), and the leg equity curves are summed on a common calendar into
one portfolio equity curve. From there the usual `compute_metrics` apply.

The point of combining strategies is diversification: if the legs' returns aren't
perfectly correlated, the blended Sharpe beats the weighted average of the legs'
Sharpes. We surface that explicitly (`diversification_ratio`, the leg-return
correlation matrix) so the benefit is visible, not assumed.

Config is data, not code: define a portfolio in a JSON file (see
portfolios/example.json) and run it with `quant portfolio --config ...`, so new
allocations don't require touching Python.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from quant.backtest.base import BacktestEngine
from quant.backtest.metrics import compute_metrics
from quant.backtest.vectorbt_engine import VectorBTEngine
from quant.strategies.registry import get_strategy_cls
from quant.utils import get_logger

log = get_logger(__name__)


@dataclass
class PortfolioLeg:
    symbol: str
    strategy: str
    params: dict = field(default_factory=dict)
    weight: float = 1.0


@dataclass
class PortfolioResult:
    equity_curve: pd.Series                       # combined portfolio value
    metrics: dict                                 # compute_metrics on the combined curve
    legs: list[PortfolioLeg]                      # legs, with weights normalized
    leg_metrics: dict[str, dict]                  # label -> compute_metrics for that leg
    correlation: pd.DataFrame                     # leg daily-return correlation matrix
    weighted_avg_sharpe: float | None             # Σ wᵢ·Sharpeᵢ (the no-diversification baseline)
    init_cash: float

    @property
    def diversification_ratio(self) -> float | None:
        """Combined Sharpe ÷ weighted-average leg Sharpe. >1 means the blend
        beat its parts (diversification paid off); ~1 means no benefit."""
        combined = self.metrics.get("sharpe")
        if combined is None or not self.weighted_avg_sharpe:
            return None
        return round(combined / self.weighted_avg_sharpe, 2)


def _leg_label(leg: PortfolioLeg) -> str:
    return f"{leg.symbol}:{leg.strategy}"


def run_portfolio(
    legs: list[PortfolioLeg],
    *,
    cash: float = 100_000,
    start: str = "2020-01-01",
    timeframe: str = "1d",
    data_map: dict[str, pd.DataFrame] | None = None,
    engine_cls: type[BacktestEngine] = VectorBTEngine,
) -> PortfolioResult:
    """Backtest each leg with its share of `cash` and combine into one curve.

    `data_map` lets callers (and tests) inject {symbol: OHLCV} to run offline;
    when omitted, each leg's bars are loaded via the normal data loader.
    `engine_cls` picks the backtest engine (default VectorBT; pass
    BacktraderEngine to run every leg through the event-driven engine).
    """
    if not legs:
        raise ValueError("portfolio needs at least one leg")

    # Normalize weights so they sum to 1 (tolerate users who don't do it by hand).
    total_w = sum(leg.weight for leg in legs)
    if total_w <= 0:
        raise ValueError("portfolio leg weights must sum to a positive number")
    legs = [PortfolioLeg(leg.symbol, leg.strategy, leg.params, leg.weight / total_w)
            for leg in legs]

    leg_equities: dict[str, pd.Series] = {}
    leg_metrics: dict[str, dict] = {}
    for leg in legs:
        data = (data_map or {}).get(leg.symbol)
        if data is None:
            data = _load_symbol(leg.symbol, start, timeframe)
        strat = get_strategy_cls(leg.strategy)(**leg.params)
        leg_cash = cash * leg.weight
        res = engine_cls(cash=leg_cash).run(strat, data, timeframe=timeframe)
        label = _leg_label(leg)
        leg_equities[label] = res.equity_curve.rename(label)
        leg_metrics[label] = res.metrics
        log.info(f"portfolio leg {label} (w={leg.weight:.2f}, cash={leg_cash:,.0f}): "
                 f"sharpe={res.metrics.get('sharpe')} ret={res.metrics.get('total_return_pct')}%")

    combined = _combine_equities(leg_equities, legs, cash)
    metrics = compute_metrics(combined, timeframe=timeframe)

    # Diversification view: correlation of leg daily returns + the no-diversification baseline.
    rets = pd.DataFrame({k: v.pct_change(fill_method=None) for k, v in leg_equities.items()})
    correlation = rets.corr()
    was = _weighted_avg_sharpe(legs, leg_metrics)

    return PortfolioResult(
        equity_curve=combined,
        metrics=metrics,
        legs=legs,
        leg_metrics=leg_metrics,
        correlation=correlation.round(2),
        weighted_avg_sharpe=was,
        init_cash=cash,
    )


def _combine_equities(
    leg_equities: dict[str, pd.Series], legs: list[PortfolioLeg], cash: float
) -> pd.Series:
    """Sum leg equity curves on a shared calendar.

    Legs may trade different symbols (slightly different calendars), so align on
    the union of dates: forward-fill each leg's last known value (its equity
    persists between its bars) and back-fill the leading gap with that leg's
    starting capital, so the total is meaningful from day one.
    """
    aligned = pd.DataFrame(leg_equities)
    by_label = {_leg_label(leg): leg for leg in legs}
    for label in aligned.columns:
        start_cash = cash * by_label[label].weight
        aligned[label] = aligned[label].ffill().fillna(start_cash)
    return aligned.sum(axis=1).rename("portfolio")


def _weighted_avg_sharpe(legs: list[PortfolioLeg], leg_metrics: dict[str, dict]) -> float | None:
    pairs = [(leg.weight, leg_metrics[_leg_label(leg)].get("sharpe")) for leg in legs]
    usable = [(w, s) for w, s in pairs if s is not None]
    if not usable:
        return None
    return round(float(np.sum([w * s for w, s in usable])), 2)


def _load_symbol(symbol: str, start: str, timeframe: str) -> pd.DataFrame:
    from quant.data.feeds.yfinance_feed import YFinanceFeed
    from quant.data.loaders import load_bars

    start_dt = datetime.fromisoformat(start).replace(tzinfo=UTC)
    return load_bars(symbol, YFinanceFeed(), start=start_dt, timeframe=timeframe)


# --- config (JSON) ---------------------------------------------------------
def load_portfolio_config(path: str | Path) -> dict:
    """Parse a portfolio JSON file into kwargs for `run_portfolio` (+ a name).

    Schema:
        {"name": "...", "cash": 100000, "start": "2020-01-01", "timeframe": "1d",
         "legs": [{"symbol": "SPY", "strategy": "momentum",
                   "params": {"lookback": 100}, "weight": 0.5}, ...]}
    """
    spec = json.loads(Path(path).read_text(encoding="utf-8"))
    legs = [
        PortfolioLeg(
            symbol=leg["symbol"],
            strategy=leg["strategy"],
            params=leg.get("params", {}),
            weight=float(leg.get("weight", 1.0)),
        )
        for leg in spec["legs"]
    ]
    return {
        "name": spec.get("name", Path(path).stem),
        "legs": legs,
        "cash": float(spec.get("cash", 100_000)),
        "start": spec.get("start", "2020-01-01"),
        "timeframe": spec.get("timeframe", "1d"),
    }
