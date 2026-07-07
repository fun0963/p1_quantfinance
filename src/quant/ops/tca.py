"""Transaction Cost Analysis — what execution actually cost (breakdown M8.8).

Every real order carries an *intended* price (the arrival/decision price — the
latest close when the runner decided) and, once filled, an *actual* average price.
The gap is slippage; add the commission and you have the all-in cost of turning a
decision into a position. TCA aggregates that across orders so you can see whether
the live system is bleeding basis points to execution — the number a backtest, which
assumes a fixed fee/slippage, can't tell you.

Sign convention: slippage is stated as **adverse** cost — positive means worse than
intended (paid up to buy, received less to sell); negative means price improvement.
Read-only: reads the OMS `orders` table via the journal.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from quant.execution.journal import TradeJournal


def slippage_bps(intended: float, fill: float, side: str) -> float:
    """Adverse slippage in basis points for one fill (positive = worse than intended)."""
    if not intended or pd.isna(intended) or pd.isna(fill):
        return float("nan")
    adverse = (fill - intended) if side.lower() == "buy" else (intended - fill)
    return adverse / intended * 1e4


def order_costs(orders: pd.DataFrame) -> pd.DataFrame:
    """Add per-order cost columns (slippage bps/$, notional, total cost) to a frame
    of FILLED orders. Non-filled rows and rows missing prices get NaN cost."""
    df = orders.copy()
    if df.empty:
        for c in ("slippage_bps", "slippage_usd", "notional", "total_cost_usd"):
            df[c] = pd.Series(dtype="float64")
        return df

    intended = pd.to_numeric(df["intended_price"], errors="coerce")
    fill = pd.to_numeric(df["avg_fill_price"], errors="coerce")
    filled_qty = pd.to_numeric(df["filled_qty"], errors="coerce").fillna(0.0)
    commission = pd.to_numeric(df["commission"], errors="coerce").fillna(0.0)
    is_buy = df["side"].astype(str).str.lower() == "buy"

    adverse_per_share = (fill - intended).where(is_buy, intended - fill)
    df["slippage_bps"] = (adverse_per_share / intended * 1e4).where(intended != 0)
    df["slippage_usd"] = adverse_per_share * filled_qty
    df["notional"] = fill * filled_qty
    df["total_cost_usd"] = df["slippage_usd"].fillna(0.0) + commission
    return df


@dataclass
class TCAReport:
    n_orders: int              # every order the OMS knows about (of those queried)
    n_filled: int
    fill_rate: float
    avg_slippage_bps: float
    median_slippage_bps: float
    worst_slippage_bps: float
    total_slippage_usd: float
    total_commission_usd: float
    total_cost_usd: float
    total_notional_usd: float
    cost_bps_of_notional: float
    per_order: pd.DataFrame

    def summary(self) -> str:
        if self.n_filled == 0:
            return f"TCA: {self.n_orders} order(s), 0 filled - no execution cost yet"
        return (
            f"TCA: {self.n_filled}/{self.n_orders} filled ({self.fill_rate:.0%}), "
            f"avg slippage {self.avg_slippage_bps:+.1f} bps "
            f"(median {self.median_slippage_bps:+.1f}, worst {self.worst_slippage_bps:+.1f}), "
            f"cost ${self.total_cost_usd:,.2f} on ${self.total_notional_usd:,.0f} notional "
            f"({self.cost_bps_of_notional:+.1f} bps), commission ${self.total_commission_usd:,.2f}"
        )


def _empty_report() -> TCAReport:
    nan = float("nan")
    return TCAReport(0, 0, nan, nan, nan, nan, 0.0, 0.0, 0.0, 0.0, nan,
                     per_order=order_costs(pd.DataFrame()))


def tca_report(journal: TradeJournal, *, strategy: str | None = None,
               limit: int = 1000) -> TCAReport:
    """Aggregate transaction-cost stats over the OMS order history."""
    orders = journal.orders(limit=limit)
    if strategy and not orders.empty:
        orders = orders[orders["strategy"] == strategy]
    if orders.empty:
        return _empty_report()

    n_orders = len(orders)
    # Count any order that executed shares — a partial fill that was later canceled or
    # expired still carries real slippage and commission that TCA must not drop.
    executed = pd.to_numeric(orders["filled_qty"], errors="coerce").fillna(0.0) > 0
    filled = orders[executed]
    costed = order_costs(filled)
    n_filled = len(costed)
    if n_filled == 0:
        rep = _empty_report()
        rep.n_orders = n_orders
        return rep

    slip = costed["slippage_bps"].dropna()
    total_notional = float(costed["notional"].sum())
    total_cost = float(costed["total_cost_usd"].sum())
    return TCAReport(
        n_orders=n_orders,
        n_filled=n_filled,
        fill_rate=n_filled / n_orders,
        avg_slippage_bps=float(slip.mean()) if not slip.empty else float("nan"),
        median_slippage_bps=float(slip.median()) if not slip.empty else float("nan"),
        worst_slippage_bps=float(slip.max()) if not slip.empty else float("nan"),
        total_slippage_usd=float(costed["slippage_usd"].sum(skipna=True)),
        total_commission_usd=float(pd.to_numeric(costed["commission"], errors="coerce").fillna(0).sum()),
        total_cost_usd=total_cost,
        total_notional_usd=total_notional,
        cost_bps_of_notional=(total_cost / total_notional * 1e4) if total_notional else float("nan"),
        per_order=costed,
    )
