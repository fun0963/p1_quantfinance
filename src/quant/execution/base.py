"""Broker abstraction — the boundary between strategy intent and the market.

Phase 1 ships only the interface plus an Alpaca *paper* implementation. Real
order routing stays behind this interface so a strategy never knows (or cares)
whether it's hitting paper, live, or a simulator.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from quant.core.types import Order


@dataclass(frozen=True, slots=True)
class Position:
    symbol: str
    qty: float
    avg_price: float


class Broker(ABC):
    """Provider-agnostic trading interface."""

    @abstractmethod
    def submit_order(self, order: Order) -> str:
        """Submit an order; return a broker order id."""

    @abstractmethod
    def get_positions(self) -> list[Position]:
        """Return current open positions."""

    @abstractmethod
    def get_cash(self) -> float:
        """Return available buying power / cash."""
