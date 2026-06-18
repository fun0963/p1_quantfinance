"""Paper-trading session — the Phase 3 event loop, wired end to end.

Drives the full live pipeline bar by bar:

    bar -> strategy signal -> RiskManager.size -> RiskGate.check -> Broker fill

Running it over historical bars is a faithful paper simulation: it exercises the
exact code path a live runner uses (just with replayed data instead of a live
feed), so the risk gate, sizing, and broker fills are all validated offline with
no API keys. Point it at `AlpacaBroker` instead of `PaperBroker` to route to real
paper trading.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from quant.backtest.metrics import compute_metrics
from quant.core.types import Order, OrderSide, OrderType, Signal, SignalType
from quant.execution.paper_broker import Fill, PaperBroker
from quant.risk.base import FixedFractionRisk, RiskManager
from quant.risk.bracket import Bracket, BracketConfig
from quant.risk.gate import RiskGate
from quant.strategies.base import BaseStrategy
from quant.utils import get_logger

log = get_logger(__name__)


@dataclass
class PaperSessionResult:
    equity_curve: pd.Series
    metrics: dict
    fills: list[Fill]
    blocked: list[tuple] = field(default_factory=list)   # (timestamp, reason)
    exit_reasons: dict = field(default_factory=dict)     # {"stop-loss": n, "take-profit": n, "signal": n}
    final_cash: float = 0.0
    final_positions: list = field(default_factory=list)


def run_paper_session(
    strategy: BaseStrategy,
    data: pd.DataFrame,
    symbol: str,
    broker: PaperBroker | None = None,
    risk_manager: RiskManager | None = None,
    gate: RiskGate | None = None,
    bracket_cfg: BracketConfig | None = None,
    timeframe: str = "1d",
) -> PaperSessionResult:
    """Replay `data` through the live pipeline; return equity, fills, and vetoes.

    If `bracket_cfg` is active, each filled entry arms a stop-loss/take-profit
    bracket that can exit the position before the strategy's own exit signal.
    """
    broker = broker or PaperBroker()
    risk_manager = risk_manager or FixedFractionRisk(fraction=0.95)
    gate = gate or RiskGate()

    signals = strategy.generate_signals(data)
    entries, exits = signals["entries"], signals["exits"]

    equity_points: dict = {}
    blocked: list[tuple] = []
    exit_reasons: dict = {"stop-loss": 0, "take-profit": 0, "signal": 0}
    bracket: Bracket | None = None
    day_start_equity = broker.equity()
    current_day = None

    def _sell(ts, qty, fill_price, why) -> bool:
        """Submit a full-position SELL at `fill_price`; returns True if filled."""
        order = Order(symbol=symbol, side=OrderSide.SELL, qty=qty, type=OrderType.MARKET)
        reason = gate.check_order(order, fill_price, qty)
        if reason:
            blocked.append((ts, f"SELL ({why}): {reason}"))
            return False
        broker.mark(symbol, fill_price)   # fill at the exit level (stop/take/close)
        broker.submit_order(order)
        return True

    for ts, bar in data.iterrows():
        broker.now = ts            # stamp fills with the bar timestamp
        # Capture the day's opening equity BEFORE marking this bar — otherwise on
        # daily bars (each bar a new day) the day's P&L would always read zero.
        day = ts.date()
        if day != current_day:
            current_day, day_start_equity = day, broker.equity()

        close = float(bar["close"])
        broker.mark(symbol, close)
        gate.report_daily_pnl(broker.equity() - day_start_equity)
        pos_qty = broker.position_qty(symbol)
        exited = False

        # 1) Bracket stop/take — intrabar, takes priority over the strategy signal.
        if pos_qty > 0 and bracket is not None:
            hit = bracket.check(float(bar["high"]), float(bar["low"]))
            if hit:
                why, fill_price = hit
                if _sell(ts, pos_qty, fill_price, why):
                    exit_reasons[why] += 1
                    bracket, exited = None, True
                    pos_qty = broker.position_qty(symbol)

        # 2) Strategy exit signal (if the bracket didn't already close us out).
        if not exited and pos_qty > 0 and bool(exits.loc[ts]):
            if _sell(ts, pos_qty, close, "signal"):
                exit_reasons["signal"] += 1
                bracket = None

        # 3) Entry (only when flat) — arm a bracket on the resulting fill.
        if bool(entries.loc[ts]) and broker.position_qty(symbol) == 0:
            signal = Signal(symbol=symbol, timestamp=ts, type=SignalType.ENTRY_LONG)
            order = risk_manager.size(signal, close, broker.get_cash())
            if order is not None:
                reason = gate.check_order(order, close, 0)
                if reason:
                    blocked.append((ts, f"BUY: {reason}"))
                else:
                    broker.mark(symbol, close)
                    broker.submit_order(order)
                    if bracket_cfg is not None and bracket_cfg.active:
                        fill_price = broker.fills[-1].price
                        bracket = Bracket(fill_price, order.qty, bracket_cfg)

        broker.mark(symbol, close)        # ensure equity is marked to the bar close
        equity_points[ts] = broker.equity()

    equity = pd.Series(equity_points, name="equity")
    if blocked:
        log.info(f"paper session: {len(blocked)} order(s) blocked by risk gate")
    return PaperSessionResult(
        equity_curve=equity,
        metrics=compute_metrics(equity, num_trades=len(broker.fills), timeframe=timeframe),
        fills=broker.fills,
        blocked=blocked,
        exit_reasons=exit_reasons,
        final_cash=broker.get_cash(),
        final_positions=broker.get_positions(),
    )
