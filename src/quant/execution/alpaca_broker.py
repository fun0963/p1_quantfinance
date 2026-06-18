"""Alpaca paper-trading broker.

Defaults to the PAPER endpoint (ALPACA_PAPER=true). A hard guard refuses to run
against the live endpoint in phase 1 so we cannot accidentally route real money
while the system is still under construction.
"""
from __future__ import annotations

from config import get_settings
from quant.core.types import Order, OrderSide
from quant.execution.base import Broker, Position
from quant.utils import get_logger

log = get_logger(__name__)


def bracket_buy_request(symbol: str, qty: float, stop_price: float, take_price: float):
    """A market BUY whose fill auto-arms a server-side OCO stop+take (bracket order).

    Both legs are required by Alpaca for a bracket; build it as a pure object so it
    can be unit-tested without hitting the network.
    """
    from alpaca.trading.enums import OrderClass, TimeInForce
    from alpaca.trading.enums import OrderSide as ASide
    from alpaca.trading.requests import (
        MarketOrderRequest,
        StopLossRequest,
        TakeProfitRequest,
    )

    return MarketOrderRequest(
        symbol=symbol, qty=qty, side=ASide.BUY, time_in_force=TimeInForce.GTC,
        order_class=OrderClass.BRACKET,
        take_profit=TakeProfitRequest(limit_price=take_price),
        stop_loss=StopLossRequest(stop_price=stop_price),
    )


def oco_sell_request(symbol: str, qty: float, stop_price: float, take_price: float):
    """An OCO SELL to protect an EXISTING long: take-profit limit + stop-loss, one cancels the other.

    Alpaca requires BOTH legs as request objects (`take_profit.limit_price` and
    `stop_loss.stop_price`) — the base order carries no limit_price for OCO.
    """
    from alpaca.trading.enums import OrderClass, TimeInForce
    from alpaca.trading.enums import OrderSide as ASide
    from alpaca.trading.requests import LimitOrderRequest, StopLossRequest, TakeProfitRequest

    return LimitOrderRequest(
        symbol=symbol, qty=qty, side=ASide.SELL, time_in_force=TimeInForce.GTC,
        order_class=OrderClass.OCO,
        take_profit=TakeProfitRequest(limit_price=take_price),
        stop_loss=StopLossRequest(stop_price=stop_price),
    )


class AlpacaBroker(Broker):
    def __init__(self) -> None:
        s = get_settings()
        if not s.alpaca_paper:
            raise RuntimeError(
                "Refusing to start: ALPACA_PAPER must be true in phase 1 "
                "(live order routing is intentionally disabled)."
            )
        if not s.alpaca_api_key:
            raise RuntimeError("ALPACA_API_KEY not set — see .env.example")
        self._key = s.alpaca_api_key
        self._secret = s.alpaca_secret_key

    def _client(self):
        from alpaca.trading.client import TradingClient
        return TradingClient(self._key, self._secret, paper=True)

    def submit_order(self, order: Order) -> str:
        from alpaca.trading.enums import OrderSide as AlpacaSide
        from alpaca.trading.requests import MarketOrderRequest

        side = AlpacaSide.BUY if order.side is OrderSide.BUY else AlpacaSide.SELL
        req = MarketOrderRequest(
            symbol=order.symbol, qty=order.qty, side=side, time_in_force="day"
        )
        resp = self._client().submit_order(req)
        log.info(f"[paper] submitted {order.side} {order.qty} {order.symbol} -> {resp.id}")
        return str(resp.id)

    def submit_bracket_buy(self, symbol: str, qty: float,
                           stop_price: float, take_price: float) -> str:
        """Market BUY with attached server-side stop-loss + take-profit (bracket)."""
        resp = self._client().submit_order(
            bracket_buy_request(symbol, qty, stop_price, take_price))
        log.info(f"[paper] bracket BUY {qty} {symbol} stop={stop_price} take={take_price} "
                 f"-> {resp.id}")
        return str(resp.id)

    def protect_position(self, symbol: str, stop_price: float, take_price: float) -> str:
        """Attach an OCO stop+take to the CURRENT long position in `symbol`.

        Alpaca OCO/stop orders require WHOLE shares, so the qty is floored; any
        fractional remainder of the position stays unprotected.
        """
        raw = next((p.qty for p in self.get_positions() if p.symbol == symbol), 0.0)
        qty = int(raw)
        if qty < 1:
            raise RuntimeError(
                f"no whole-share long position in {symbol} to protect (have {raw})")
        resp = self._client().submit_order(
            oco_sell_request(symbol, qty, stop_price, take_price))
        log.info(f"[paper] OCO protect {qty} {symbol} stop={stop_price} take={take_price} "
                 f"-> {resp.id}")
        return str(resp.id)

    def get_positions(self) -> list[Position]:
        return [
            Position(symbol=p.symbol, qty=float(p.qty), avg_price=float(p.avg_entry_price))
            for p in self._client().get_all_positions()
        ]

    def get_cash(self) -> float:
        return float(self._client().get_account().cash)

    def account_summary(self) -> dict:
        """Read-only snapshot of the connected account (for connection checks)."""
        acct = self._client().get_account()
        return {
            "account_number": acct.account_number,
            "status": str(acct.status),
            "currency": acct.currency,
            "cash": float(acct.cash),
            "equity": float(acct.equity),
            "buying_power": float(acct.buying_power),
            "is_paper": getattr(acct, "is_paper", get_settings().alpaca_paper),
        }
