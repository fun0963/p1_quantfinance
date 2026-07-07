"""Reconciliation — compare the broker's actual book against the audit trail.

The fail-safe backbone (breakdown M8.6, audit P0 #6): before the live runner
trades, and on a daily check, confirm the broker's positions/orders match what the
journal says the system did. A mismatch — a position we never opened, a naked
position with no protective order, or an order for a symbol we don't hold — means
the system is out of sync with reality. The safe response is to STOP and alert, not
to keep acting on a book we don't understand.

Reconciliation is read-only: it reports; the caller decides to halt/alert.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from quant.execution.base import Broker
from quant.execution.journal import TradeJournal
from quant.utils import get_logger

log = get_logger(__name__)


@dataclass
class ReconcileIssue:
    symbol: str
    kind: str        # untracked_position | unprotected_position | orphan_order
    severity: str    # WARN | CRITICAL
    detail: str


@dataclass
class ReconcileReport:
    ok: bool                       # False when there's a CRITICAL issue (unsafe to trade)
    issues: list[ReconcileIssue]
    positions: dict               # symbol -> qty at the broker
    checked_at: str

    @property
    def critical(self) -> list[ReconcileIssue]:
        return [i for i in self.issues if i.severity == "CRITICAL"]

    def summary(self) -> str:
        if not self.issues:
            return "reconcile: clean"
        return f"reconcile: {len(self.issues)} issue(s), {len(self.critical)} critical"


def _bought_symbols(journal: TradeJournal) -> set[str]:
    """Symbols the journal has an EXECUTED buy record for."""
    df = journal.live_log(limit=10_000)
    if df.empty:
        return set()
    bought = df[(df["action"] == "buy") & df["order_id"].notna()]
    return set(bought["symbol"].unique())


def reconcile(broker: Broker, journal: TradeJournal) -> ReconcileReport:
    """Check the broker's positions/orders against the journal. Read-only."""
    positions = {p.symbol: p.qty for p in broker.get_positions() if abs(p.qty) > 1e-9}
    open_orders: list[dict] = []
    if hasattr(broker, "get_open_orders"):
        try:
            open_orders = broker.get_open_orders()
        except Exception as exc:  # noqa: BLE001 - reconciliation must not crash
            log.warning(f"reconcile: could not read open orders: {exc}")

    bought = _bought_symbols(journal)
    protected = {o["symbol"] for o in open_orders if str(o.get("side", "")).lower() == "sell"}
    issues: list[ReconcileIssue] = []

    # 1. CRITICAL — broker holds a symbol the system never bought (out of sync / manual / bug).
    for sym, qty in positions.items():
        if sym not in bought:
            issues.append(ReconcileIssue(
                sym, "untracked_position", "CRITICAL",
                f"broker holds {qty:g} {sym} but the journal has no executed buy for it"))
        # 2. WARN — a held position with no protective (stop/OCO) sell order.
        elif sym not in protected:
            issues.append(ReconcileIssue(
                sym, "unprotected_position", "WARN",
                f"{qty:g} {sym} held with no protective (stop/OCO) order"))

    # 3. WARN — an open order for a symbol we don't hold (orphan / leftover).
    for o in open_orders:
        osym = o.get("symbol")
        if osym and osym not in positions:
            issues.append(ReconcileIssue(
                osym, "orphan_order", "WARN",
                f"open {o.get('side')} order on {osym} but no position held"))

    ok = not any(i.severity == "CRITICAL" for i in issues)
    return ReconcileReport(ok=ok, issues=issues, positions=positions,
                           checked_at=datetime.now(UTC).isoformat(timespec="seconds"))
