"""Backtrader engine — event-driven, live-like validation.

Bridge design: we precompute the strategy's boolean entries/exits ONCE (so the
signals are byte-identical to what VectorBT saw), feed them to Backtrader as
extra data lines, and a thin bt.Strategy acts on them bar by bar. This isolates
the only intended difference between the two engines — execution modeling — from
the strategy logic itself.

Fill model: `cheat-on-close` is enabled so entries fill at the signal bar's
close, matching VectorBT's same-bar fill. Disable it (set_coc False) to get the
more conservative next-bar-open fill instead.
"""
from __future__ import annotations

import pandas as pd

from quant.backtest.base import BacktestEngine, BacktestResult
from quant.backtest.metrics import compute_metrics
from quant.strategies.base import BaseStrategy
from quant.utils import get_logger

log = get_logger(__name__)


def _build_feed(data: pd.DataFrame, signals: pd.DataFrame):
    """Merge OHLCV + boolean signals into a Backtrader PandasData with extra lines."""
    import backtrader as bt

    merged = data.copy()
    merged["entries"] = signals["entries"].astype(int)
    merged["exits"] = signals["exits"].astype(int)

    class _SignalData(bt.feeds.PandasData):
        lines = ("entries", "exits")
        params = (
            ("datetime", None),  # use the DatetimeIndex
            ("open", "open"),
            ("high", "high"),
            ("low", "low"),
            ("close", "close"),
            ("volume", "volume"),
            ("openinterest", None),
            ("entries", "entries"),
            ("exits", "exits"),
        )

    # Backtrader wants a tz-naive index.
    if merged.index.tz is not None:
        merged.index = merged.index.tz_localize(None)
    return _SignalData(dataname=merged)


def _make_strategy_cls(target_pct: float, equity_log: list):
    """A bt.Strategy that trades the precomputed entries/exits lines."""
    import backtrader as bt

    class _BridgeStrategy(bt.Strategy):
        def next(self):
            equity_log.append((self.data.datetime.datetime(0), self.broker.getvalue()))
            if self.data.entries[0] > 0 and not self.position:
                self.order_target_percent(target=target_pct)
            elif self.data.exits[0] > 0 and self.position:
                self.close()

    return _BridgeStrategy


class BacktraderEngine(BacktestEngine):
    name = "backtrader"

    def __init__(self, cash: float = 100_000, fees: float = 0.0005, target_pct: float = 0.99):
        super().__init__(cash=cash, fees=fees)
        self.target_pct = target_pct  # fraction of equity per position (≈ all-in by default)

    def run(
        self, strategy: BaseStrategy, data: pd.DataFrame, timeframe: str = "1d"
    ) -> BacktestResult:
        import backtrader as bt

        signals = strategy.generate_signals(data)
        log.info(f"Running {strategy} on backtrader ({len(data)} bars)")

        equity_log: list[tuple] = []
        cerebro = bt.Cerebro()
        cerebro.addstrategy(_make_strategy_cls(self.target_pct, equity_log))
        cerebro.adddata(_build_feed(data, signals))
        cerebro.broker.setcash(self.cash)
        cerebro.broker.setcommission(commission=self.fees)
        cerebro.broker.set_coc(True)  # cheat-on-close → same-bar fill, matches vectorbt
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

        results = cerebro.run()
        trade_ana = results[0].analyzers.trades.get_analysis()
        num_trades = int(trade_ana.get("total", {}).get("closed", 0))

        equity = pd.Series(
            {ts: val for ts, val in equity_log}, name="equity"
        ).sort_index()

        return BacktestResult(
            equity_curve=equity,
            metrics=compute_metrics(equity, num_trades=num_trades, timeframe=timeframe),
            stats=dict(trade_ana),
            trades=None,
            engine=self.name,
        )
