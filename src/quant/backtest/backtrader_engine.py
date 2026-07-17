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


def _build_feed(data: pd.DataFrame, signals: pd.DataFrame, timeframe: str = "1d"):
    """Merge OHLCV + boolean signals into a Backtrader PandasData with extra lines."""
    import backtrader as bt

    from quant.data.timeframes import get_timeframe

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
    tf = get_timeframe(timeframe)
    if tf.intraday:
        # Label bars as minutes so bt's datetime handling doesn't assume days.
        return _SignalData(dataname=merged, timeframe=bt.TimeFrame.Minutes,
                           compression=max(1, tf.bar_seconds // 60))
    return _SignalData(dataname=merged)


def _make_strategy_cls(target_pct: float, equity_log: list, trades_log: list):
    """A bt.Strategy that trades the precomputed entries/exits lines.

    `equity_log` collects (datetime, value) each bar; `trades_log` collects one
    dict per *closed* trade so the engine can return a per-trade table (for TCA
    and trade_stats), matching what the VectorBT engine already provides.
    """
    import backtrader as bt

    class _BridgeStrategy(bt.Strategy):
        def next(self):
            equity_log.append((self.data.datetime.datetime(0), self.broker.getvalue()))
            if self.data.entries[0] > 0 and not self.position:
                self.order_target_percent(target=target_pct)
            elif self.data.exits[0] > 0 and self.position:
                self.close()

        def notify_trade(self, trade):
            if not trade.isclosed:
                return
            # PnL uses pnlcomm (net of commission) so win-rate/payoff line up with
            # VectorBT's 'PnL' column; gross pnl and commission are kept alongside.
            trades_log.append({
                "entry_time": bt.num2date(trade.dtopen),
                "exit_time": bt.num2date(trade.dtclose),
                "entry_price": round(float(trade.price), 4),
                "bars_held": int(trade.barlen),
                "pnl": round(float(trade.pnl), 4),
                "PnL": round(float(trade.pnlcomm), 4),
                "commission": round(float(trade.commission), 4),
            })

    return _BridgeStrategy


class BacktraderEngine(BacktestEngine):
    name = "backtrader"

    def __init__(self, cash: float = 100_000, fees: float = 0.0005,
                 slippage: float = 0.0, target_pct: float = 0.99):
        super().__init__(cash=cash, fees=fees, slippage=slippage)
        self.target_pct = target_pct  # fraction of equity per position (≈ all-in by default)

    def run(
        self, strategy: BaseStrategy, data: pd.DataFrame, timeframe: str = "1d"
    ) -> BacktestResult:
        import backtrader as bt

        signals = strategy.generate_signals(data)
        log.info(f"Running {strategy} on backtrader ({len(data)} bars)")

        equity_log: list[tuple] = []
        trades_log: list[dict] = []
        cerebro = bt.Cerebro()
        cerebro.addstrategy(_make_strategy_cls(self.target_pct, equity_log, trades_log))
        cerebro.adddata(_build_feed(data, signals, timeframe))
        cerebro.broker.setcash(self.cash)
        # Slippage is folded into commission (both are per-side fractions of
        # notional). Backtrader's set_slippage_perc is unreliable under
        # cheat-on-close, and charging fees+slippage as one commission is P&L-
        # equivalent per side and keeps total cost in step with the VectorBT engine.
        cerebro.broker.setcommission(commission=self.fees + self.slippage)
        cerebro.broker.set_coc(True)  # cheat-on-close → same-bar fill, matches vectorbt
        cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="trades")

        results = cerebro.run()
        trade_ana = results[0].analyzers.trades.get_analysis()

        equity = pd.Series(
            {ts: val for ts, val in equity_log}, name="equity"
        ).sort_index()

        # Per-trade table (was None): entry/exit time+price, bars held, net/gross PnL.
        trades = pd.DataFrame(
            trades_log,
            columns=["entry_time", "exit_time", "entry_price", "bars_held",
                     "pnl", "PnL", "commission"],
        )

        return BacktestResult(
            equity_curve=equity,
            metrics=compute_metrics(equity, num_trades=len(trades), timeframe=timeframe),
            stats=dict(trade_ana),
            trades=trades,
            engine=self.name,
        )
