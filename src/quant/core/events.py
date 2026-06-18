"""Event types for the (future) live event loop.

Reserved for phase 3: a live runner consumes MarketEvents, strategies emit
SignalEvents, risk produces OrderEvents, execution emits FillEvents. Defined now
so the architecture is explicit; not yet wired into a dispatcher.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from quant.core.types import Bar, Order, Signal


class EventType(str, Enum):
    MARKET = "market"
    SIGNAL = "signal"
    ORDER = "order"
    FILL = "fill"


@dataclass(frozen=True, slots=True)
class MarketEvent:
    bar: Bar
    type: EventType = EventType.MARKET


@dataclass(frozen=True, slots=True)
class SignalEvent:
    signal: Signal
    type: EventType = EventType.SIGNAL


@dataclass(frozen=True, slots=True)
class OrderEvent:
    order: Order
    type: EventType = EventType.ORDER


@dataclass(frozen=True, slots=True)
class FillEvent:
    symbol: str
    qty: float
    price: float
    order_id: str
    type: EventType = EventType.FILL
