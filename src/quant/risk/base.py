"""Risk layer — sits between signals and orders.

Phase 1 ships the interface plus a simple fixed-fraction sizer so the wiring
(Signal -> RiskManager.size -> Order) exists end to end. Stop-loss, max-position,
exposure and drawdown limits are layered in here in later phases without
touching strategy or execution code.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from quant.core.types import Order, OrderSide, OrderType, Signal, SignalType


class RiskManager(ABC):
    """Turns a Signal into a sized Order (or rejects it)."""

    @abstractmethod
    def size(self, signal: Signal, price: float, cash: float) -> Order | None:
        """Return an Order for `signal`, or None to skip (risk veto)."""


class FixedFractionRisk(RiskManager):
    """Allocate a fixed fraction of cash per entry. Minimal but functional."""

    def __init__(self, fraction: float = 0.1) -> None:
        if not 0 < fraction <= 1:
            raise ValueError("fraction must be in (0, 1]")
        self.fraction = fraction

    def size(self, signal: Signal, price: float, cash: float) -> Order | None:
        if signal.type is not SignalType.ENTRY_LONG:
            return None  # phase 1: long entries only
        qty = (cash * self.fraction) / price
        if qty <= 0:
            return None
        return Order(
            symbol=signal.symbol,
            side=OrderSide.BUY,
            qty=round(qty, 4),
            type=OrderType.MARKET,
        )
