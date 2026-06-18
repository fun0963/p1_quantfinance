"""VectorBT engine — fast vectorized backtests for research & parameter sweeps.

Consumes the boolean entries/exits a strategy emits and builds a portfolio in
one shot. Orders of magnitude faster than event-driven; use it to narrow the
search space, then confirm survivors with the Backtrader engine.
"""
from __future__ import annotations

import pandas as pd

from quant.backtest.base import BacktestEngine, BacktestResult
from quant.backtest.metrics import compute_metrics
from quant.strategies.base import BaseStrategy
from quant.utils import get_logger

log = get_logger(__name__)


class VectorBTEngine(BacktestEngine):
    name = "vectorbt"

    def run(
        self, strategy: BaseStrategy, data: pd.DataFrame, timeframe: str = "1d"
    ) -> BacktestResult:
        import vectorbt as vbt  # lazy import

        signals = strategy.generate_signals(data)
        log.info(f"Running {strategy} on vectorbt ({len(data)} bars)")

        pf = vbt.Portfolio.from_signals(
            close=data["close"],
            entries=signals["entries"],
            exits=signals["exits"],
            init_cash=self.cash,
            fees=self.fees,
            freq="1D",
        )
        stats = pf.stats()
        equity = pf.value()
        trades = pf.trades.records_readable
        return BacktestResult(
            equity_curve=equity,
            metrics=compute_metrics(equity, num_trades=len(trades), timeframe=timeframe),
            stats=stats.to_dict() if hasattr(stats, "to_dict") else dict(stats),
            trades=trades,
            engine=self.name,
        )
