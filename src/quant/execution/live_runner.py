"""Live runner — one decision on the latest bar, routed to a real broker.

Unlike the backtest/paper-session (which replay history), the live runner is
*stateless between runs*: the broker's actual positions are the source of truth
(reconciliation), so it's safe to run once per bar / on a schedule. It evaluates
the strategy on recent history, looks only at the LATEST bar's signal, passes any
order through the same `RiskGate`, and submits via the given `Broker`.

Safety: `dry_run=True` (the default) computes and reports the decision WITHOUT
submitting. Real order routing only happens with `dry_run=False`.

Scope (v1): long entry/exit on the latest signal + risk gate. Protective stops in
live mode are a follow-up — the intended path is Alpaca's native bracket orders
(server-side OCO), not the client-side trigger loop used in backtests.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pandas as pd

from quant.core.types import Order, OrderSide, OrderType, Signal, SignalType
from quant.execution.base import Broker
from quant.risk.base import FixedFractionRisk, RiskManager
from quant.risk.gate import RiskGate
from quant.strategies.base import BaseStrategy
from quant.utils import get_logger

if TYPE_CHECKING:
    from quant.risk.bracket import BracketConfig

log = get_logger(__name__)


@dataclass
class LiveDecision:
    ts: object                 # timestamp of the bar the decision was made on
    symbol: str
    action: str                # 'buy' | 'sell' | 'hold' | 'flat'
    price: float               # latest close (sizing/gate reference)
    qty: float = 0.0
    reason: str = ""           # why this action
    order_id: str | None = None
    blocked: str | None = None  # risk-gate veto reason, if any
    dry_run: bool = True
    position_before: float = 0.0
    target_state: str | None = None   # 'long' | 'flat' (target mode only)


def _position_qty(broker: Broker, symbol: str) -> float:
    for p in broker.get_positions():
        if p.symbol == symbol:
            return p.qty
    return 0.0


def _target_state(signals: pd.DataFrame) -> int | None:
    """Desired long-only position as of the LATEST bar: 1 = long, 0 = flat,
    or **None = no opinion** (the strategy never signalled anything in the window).

    Replays the boolean entries/exits into a held state (entry→long, exit→flat,
    forward-filled), so the runner reconciles to the strategy's *intended* position
    rather than only firing on the crossover bar.

    Critical: if there are NO entries and NO exits at all (indicators still warming
    up, too little data, or a bug producing an all-False frame), we return None so
    the caller HOLDS — an empty signal frame must never be read as "target flat" and
    used to liquidate a real position.
    """
    entries = signals["entries"].astype(bool)
    exits = signals["exits"].astype(bool)
    if not (entries.any() or exits.any()):
        return None
    marks = pd.Series(float("nan"), index=signals.index)
    marks[entries] = 1.0
    marks[exits] = 0.0   # exit wins if a bar somehow has both
    state = marks.ffill().fillna(0.0)
    return int(state.iloc[-1])


def _has_open_buy(broker: Broker, symbol: str) -> bool:
    """Whether an as-yet-unfilled BUY for `symbol` already exists at the broker —
    prevents a second entry firing before the first async fill updates the position."""
    if not hasattr(broker, "get_open_orders"):
        return False
    try:
        return any(str(o.get("side", "")).lower() == "buy"
                   for o in broker.get_open_orders(symbol))
    except Exception as exc:  # noqa: BLE001 - reconciliation must not crash the step
        log.warning(f"[live] could not check open orders for {symbol}: {exc}")
        return False


def _cancel_protection(broker: Broker, symbol: str) -> None:
    """Cancel any open orders (e.g. OCO stop/take legs) holding the shares, so a
    strategy exit sell isn't rejected by the broker for insufficient quantity."""
    if not hasattr(broker, "cancel_open_orders"):
        return
    try:
        broker.cancel_open_orders(symbol)
    except Exception as exc:  # noqa: BLE001
        log.warning(f"[live] could not cancel protective orders on {symbol}: {exc}")


def run_live_step(
    strategy: BaseStrategy,
    data: pd.DataFrame,
    symbol: str,
    broker: Broker,
    risk_manager: RiskManager | None = None,
    gate: RiskGate | None = None,
    dry_run: bool = True,
    mode: str = "target",
    bracket_cfg: BracketConfig | None = None,
    now: datetime | None = None,
    max_bar_age_days: int | None = None,
    max_bar_age_seconds: float | None = None,
) -> LiveDecision:
    """Evaluate the latest bar and act once. Broker positions are the truth.

    mode='target' (default): reconcile to the strategy's *desired* position as of
        the latest bar — buy if it wants long and we're flat, sell if it wants flat
        and we hold. Robust to starting mid-trend or skipping a run.
    mode='signal': act only on the latest bar's edge (entry/exit crossover). Will
        do nothing between crossovers even if the strategy 'wants' to be long.

    If `bracket_cfg` has both legs and the broker supports it (AlpacaBroker), a
    BUY is submitted as a server-side bracket order (auto stop-loss + take-profit).
    """
    from quant.risk.bracket import bracket_prices
    if mode not in ("target", "signal"):
        raise ValueError("mode must be 'target' or 'signal'")
    risk_manager = risk_manager or FixedFractionRisk(fraction=0.95)
    gate = gate or RiskGate()

    if data.empty:
        raise ValueError("no data to evaluate")

    signals = strategy.generate_signals(data)
    ts = data.index[-1]
    price = float(data["close"].iloc[-1])
    pos = _position_qty(broker, symbol)

    # PaperBroker fills at the last marked price; harmless for AlpacaBroker.
    if hasattr(broker, "mark"):
        broker.mark(symbol, price)

    dec = LiveDecision(ts=ts, symbol=symbol, action="hold" if pos > 0 else "flat",
                       price=price, dry_run=dry_run, position_before=pos)

    # Freshness gate: never act on a stale bar — the safety net against a cached
    # run deciding on old data. Seconds-based check (intraday) wins over the
    # day-based one; a "days" tolerance is meaningless when a bar lasts a minute.
    if max_bar_age_seconds is not None:
        ts_utc = pd.Timestamp(ts)
        ts_utc = ts_utc.tz_localize(UTC) if ts_utc.tz is None else ts_utc.tz_convert(UTC)
        age_s = (pd.Timestamp(now or datetime.now(UTC)) - ts_utc).total_seconds()
        if age_s > max_bar_age_seconds:
            dec.reason = (f"stale data: latest bar {ts} is {age_s:.0f}s old "
                          f"(> {max_bar_age_seconds:.0f}s) - not acting")
            dec.blocked = dec.reason
            log.warning(f"[live] {symbol} BLOCKED: {dec.reason}")
            return dec
    elif max_bar_age_days is not None:
        age = ((now or datetime.now(UTC)).date() - ts.date()).days
        if age > max_bar_age_days:
            dec.reason = (f"stale data: latest bar {ts.date()} is {age}d old "
                          f"(> {max_bar_age_days}d) - not acting")
            dec.blocked = dec.reason
            log.warning(f"[live] {symbol} BLOCKED: {dec.reason}")
            return dec

    if mode == "target":
        target = _target_state(signals)
        if target is None:
            dec.target_state = "no signal (hold)"
            want_entry = want_exit = False
            entry_why = exit_why = ""
        else:
            dec.target_state = "long" if target == 1 else "flat"
            want_entry = target == 1 and pos == 0
            want_exit = target == 0 and pos > 0
            entry_why = f"target long, currently flat -> enter (last bar {ts.date()})"
            exit_why = "target flat, currently long -> exit"
    else:  # signal mode
        want_entry = bool(signals["entries"].iloc[-1]) and pos == 0
        want_exit = bool(signals["exits"].iloc[-1]) and pos > 0
        entry_why = "entry signal on latest bar"
        exit_why = "exit signal on latest bar"

    # A bracket (stop+take) is only applied to entries, and only when both legs
    # are set and the broker supports server-side brackets.
    use_bracket = (
        bracket_cfg is not None and bracket_cfg.stop_pct > 0 and bracket_cfg.take_pct > 0
        and hasattr(broker, "submit_bracket_buy")
    )

    def _submit(order: Order, action: str, why: str, bracket: tuple | None = None) -> None:
        reason = gate.check_order(order, price, pos)
        dec.action, dec.qty, dec.reason = action, order.qty, why
        if reason:
            dec.blocked = reason
            log.warning(f"[live] {action} {order.qty} {symbol} BLOCKED: {reason}")
            return
        if dry_run:
            extra = f" + bracket(stop={bracket[0]}, take={bracket[1]})" if bracket else ""
            dec.reason = f"{why}{extra} (dry-run, not submitted)"
            log.info(f"[live][dry-run] would {action} {order.qty} {symbol} @ ~{price:.2f}{extra}")
            return
        if hasattr(broker, "mark"):
            broker.mark(symbol, price)
        if bracket is not None:
            # submit_bracket_buy only exists on brokers that support it; use_bracket
            # already gated on hasattr(broker, "submit_bracket_buy").
            dec.order_id = broker.submit_bracket_buy(  # type: ignore[attr-defined]
                symbol, order.qty, bracket[0], bracket[1])
        else:
            dec.order_id = broker.submit_order(order)
        log.info(f"[live] {action} {order.qty} {symbol} -> order {dec.order_id}")

    if want_entry:
        # Don't fire a second entry while a prior BUY is still unfilled (async fills
        # mean the position query can still read flat right after a submit).
        if _has_open_buy(broker, symbol):
            dec.reason = "entry skipped: an unfilled BUY for this symbol already exists"
            log.info(f"[live] {symbol} {dec.reason}")
            return dec
        signal = Signal(symbol=symbol, timestamp=ts, type=SignalType.ENTRY_LONG)
        order = risk_manager.size(signal, price, broker.get_cash())
        if order is not None:
            bracket = None
            if use_bracket:
                assert bracket_cfg is not None  # implied by use_bracket
                bracket = bracket_prices(price, bracket_cfg.stop_pct, bracket_cfg.take_pct)
                # Alpaca bracket/OCO require WHOLE shares (no fractional).
                whole = int(order.qty)
                if whole < 1:
                    dec.action = "flat"
                    dec.reason = "entry skipped: <1 whole share, bracket needs whole shares"
                    return dec
                order = replace(order, qty=float(whole))
            _submit(order, "buy", entry_why, bracket=bracket)
    elif want_exit:
        # Cancel any protective OCO legs first (they hold the shares); otherwise the
        # exit sell is rejected for insufficient quantity. Only when really trading.
        if not dry_run:
            _cancel_protection(broker, symbol)
        order = Order(symbol=symbol, side=OrderSide.SELL, qty=pos, type=OrderType.MARKET)
        _submit(order, "sell", exit_why)

    return dec
