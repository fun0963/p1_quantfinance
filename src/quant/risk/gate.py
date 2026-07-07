"""Pre-trade risk gate — the safety layer every order passes before it's sent.

Design mirrors the kill-switch pattern from a production trading terminal: every
order path calls `check_order()` first; a breach (per-order cap, position cap,
daily-loss limit, or the manual lock) blocks the order and returns a reason. This
is distinct from `RiskManager` (which *sizes* orders) — the gate *vetoes* them.

Phase 3 wires this into the paper-trading session; the same gate guards live
order routing when that's switched on.
"""
from __future__ import annotations

from dataclasses import dataclass

from quant.core.types import Order, OrderSide


@dataclass
class RiskLimits:
    """Risk caps. A limit of 0 means 'unlimited' (disabled)."""
    enabled: bool = True                 # master switch for the numeric rules
    max_order_qty: float = 0             # per-order quantity cap
    max_order_notional: float = 0        # per-order value cap (qty * price)
    max_position_notional: float = 0     # cap on resulting per-symbol position value
    max_daily_loss: float = 0            # positive; block when daily P&L <= -this
    locked: bool = False                 # manual kill switch — blocks ALL orders


class RiskGate:
    """Stateful pre-trade gate. `check_order` returns a block reason, or None if OK."""

    def __init__(self, limits: RiskLimits | None = None) -> None:
        self.limits = limits or RiskLimits()
        self._daily_pnl = 0.0

    def report_daily_pnl(self, pnl: float) -> None:
        """Feed the running day's P&L (the session/account polling updates this)."""
        self._daily_pnl = pnl

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    def lock(self) -> None:
        self.limits.locked = True

    def unlock(self) -> None:
        self.limits.locked = False

    def check_order(
        self,
        order: Order,
        price: float,
        current_position_qty: float = 0.0,
    ) -> str | None:
        """Return a human-readable block reason, or None when the order is allowed."""
        lim = self.limits

        # The manual kill switch overrides everything, even when rules are disabled.
        if lim.locked:
            return "kill switch engaged — all orders blocked"
        if not lim.enabled:
            return None

        # Risk-REDUCING orders (shrinking the absolute position) must always be
        # allowed to pass the automated numeric limits — you must be able to cut
        # risk even when the daily-loss breaker or a position cap has tripped.
        # Only the manual kill switch (above) stops them.
        signed = order.qty if order.side is OrderSide.BUY else -order.qty
        if abs(current_position_qty + signed) < abs(current_position_qty):
            return None

        if lim.max_order_qty and order.qty > lim.max_order_qty:
            return f"order qty {order.qty} exceeds per-order cap {lim.max_order_qty}"

        notional = order.qty * price
        if lim.max_order_notional and notional > lim.max_order_notional:
            return (f"order notional {notional:.0f} exceeds per-order cap "
                    f"{lim.max_order_notional:.0f}")

        if lim.max_position_notional:
            signed = order.qty if order.side is OrderSide.BUY else -order.qty
            projected = abs(current_position_qty + signed) * price
            if projected > lim.max_position_notional:
                return (f"resulting position {projected:.0f} exceeds cap "
                        f"{lim.max_position_notional:.0f}")

        if lim.max_daily_loss and self._daily_pnl <= -lim.max_daily_loss:
            return (f"daily loss {self._daily_pnl:.0f} hit limit "
                    f"-{lim.max_daily_loss:.0f} — orders blocked")

        return None
