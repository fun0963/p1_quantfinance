"""In-memory paper broker — a deterministic fill simulator.

Implements the same `Broker` interface as `AlpacaBroker`, so the live/paper
pipeline can run and be unit-tested with zero external dependencies or API keys.
Market orders fill immediately at the symbol's last marked price, with a fee and
optional slippage. Swap this for `AlpacaBroker` to route to real paper trading.
"""
from __future__ import annotations

from dataclasses import dataclass

from quant.core.types import Order, OrderSide
from quant.execution.base import Broker, Position
from quant.utils import get_logger

log = get_logger(__name__)


@dataclass
class _Holding:
    qty: float = 0.0
    avg_price: float = 0.0


@dataclass
class Fill:
    order_id: str
    symbol: str
    side: OrderSide
    qty: float
    price: float
    ts: object = None        # bar timestamp at fill (set via PaperBroker.now)


class PaperBroker(Broker):
    def __init__(self, cash: float = 100_000, fees: float = 0.0005, slippage: float = 0.0):
        self._cash = float(cash)
        self.fees = fees
        self.slippage = slippage          # fraction, applied against the taker
        self._prices: dict[str, float] = {}
        self._book: dict[str, _Holding] = {}
        self.fills: list[Fill] = []
        self._seq = 0
        self.now = None              # current timestamp, stamped onto fills

    # --- price feed -------------------------------------------------------
    def mark(self, symbol: str, price: float) -> None:
        """Update the last known price used to fill subsequent market orders."""
        self._prices[symbol] = float(price)

    # --- Broker interface -------------------------------------------------
    def submit_order(self, order: Order) -> str:
        price = self._prices.get(order.symbol)
        if price is None:
            raise RuntimeError(f"no marked price for {order.symbol}; call mark() first")

        # Slippage works against you: pay up to buy, receive less to sell.
        fill_price = price * (1 + self.slippage) if order.side is OrderSide.BUY \
            else price * (1 - self.slippage)
        gross = order.qty * fill_price
        fee = gross * self.fees

        h = self._book.setdefault(order.symbol, _Holding())
        if order.side is OrderSide.BUY:
            new_qty = h.qty + order.qty
            h.avg_price = (h.avg_price * h.qty + gross) / new_qty if new_qty else 0.0
            h.qty = new_qty
            self._cash -= gross + fee
        else:  # SELL
            h.qty -= order.qty
            self._cash += gross - fee
            if abs(h.qty) < 1e-9:
                h.qty, h.avg_price = 0.0, 0.0

        self._seq += 1
        oid = f"paper-{self._seq}"
        self.fills.append(Fill(oid, order.symbol, order.side, order.qty, fill_price, ts=self.now))
        log.debug(f"[paper] {order.side.value} {order.qty} {order.symbol} @ {fill_price:.2f}")
        return oid

    def get_positions(self) -> list[Position]:
        return [Position(sym, h.qty, h.avg_price)
                for sym, h in self._book.items() if abs(h.qty) > 1e-9]

    def get_cash(self) -> float:
        return self._cash

    # --- reconciliation surface (parity with AlpacaBroker) ----------------
    def get_open_orders(self, symbol: str | None = None) -> list[dict]:
        """PaperBroker fills market orders synchronously, so there are never any
        pending orders — always empty. Present for interface parity with the live
        path's open-order / cancel-before-exit logic."""
        return []

    def cancel_open_orders(self, symbol: str) -> int:
        return 0

    # --- helpers ----------------------------------------------------------
    def position_qty(self, symbol: str) -> float:
        h = self._book.get(symbol)
        return h.qty if h else 0.0

    def equity(self) -> float:
        """Cash + marked-to-market value of all holdings."""
        mtm = sum(h.qty * self._prices.get(sym, h.avg_price)
                  for sym, h in self._book.items())
        return self._cash + mtm
