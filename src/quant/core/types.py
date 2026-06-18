"""Domain types — the lingua franca passed between layers.

Strategies emit `Signal`s; execution turns them into `Order`s; data feeds yield
`Bar`s. Keeping these provider-agnostic is what lets us swap Alpaca/yfinance or
VectorBT/Backtrader without touching strategy code.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class SignalType(str, Enum):
    ENTRY_LONG = "entry_long"
    EXIT_LONG = "exit_long"
    ENTRY_SHORT = "entry_short"
    EXIT_SHORT = "exit_short"


@dataclass(frozen=True, slots=True)
class Bar:
    """A single OHLCV candle for one symbol."""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True, slots=True)
class Signal:
    """A strategy's intent, independent of position sizing or broker."""
    symbol: str
    timestamp: datetime
    type: SignalType
    strength: float = 1.0          # 0..1, for weighting / sizing downstream
    meta: dict | None = None


@dataclass(frozen=True, slots=True)
class Order:
    """A concrete instruction handed to a Broker."""
    symbol: str
    side: OrderSide
    qty: float
    type: OrderType = OrderType.MARKET
    limit_price: float | None = None
    client_id: str | None = None
