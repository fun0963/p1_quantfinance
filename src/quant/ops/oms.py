"""Order Management System — the lifecycle of every real order (breakdown M8.3).

The live runner *submits* an order and moves on; fills arrive asynchronously at the
broker. The OMS is the persistent state machine that tracks each order from
SUBMITTED through to a terminal state (FILLED / CANCELED / REJECTED / EXPIRED),
recording every transition. That record is what TCA reads (intended vs actual fill)
and what reconciliation and the daily report lean on to answer "what happened to the
order we sent?".

Design:
  * One `orders` row per order, plus an append-only `order_events` audit trail of
    state transitions (both live in the journal DB — see journal.py schema).
  * `on_submit()` records a freshly-placed order (NEW -> SUBMITTED).
  * `sync(broker)` polls the broker for each non-terminal order and advances its
    state — this is how an async fill becomes FILLED with its real price/commission.
  * Illegal transitions (e.g. out of a terminal state) are refused and logged, never
    silently applied: the audit trail must stay trustworthy.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum

from quant.execution.journal import TradeJournal
from quant.utils import get_logger

log = get_logger(__name__)


class OrderState(str, Enum):
    NEW = "NEW"                          # created locally, not yet acknowledged
    SUBMITTED = "SUBMITTED"              # accepted by the broker, working
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"                    # terminal
    CANCELED = "CANCELED"                # terminal
    REJECTED = "REJECTED"                # terminal
    EXPIRED = "EXPIRED"                  # terminal


_TERMINAL: frozenset[OrderState] = frozenset(
    {OrderState.FILLED, OrderState.CANCELED, OrderState.REJECTED, OrderState.EXPIRED}
)

# Which transitions are allowed. A terminal state has no outgoing edges.
_LEGAL: dict[OrderState, frozenset[OrderState]] = {
    OrderState.NEW: frozenset(
        {OrderState.SUBMITTED, OrderState.REJECTED, OrderState.CANCELED}),
    OrderState.SUBMITTED: frozenset(
        {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCELED,
         OrderState.REJECTED, OrderState.EXPIRED}),
    OrderState.PARTIALLY_FILLED: frozenset(
        {OrderState.PARTIALLY_FILLED, OrderState.FILLED, OrderState.CANCELED,
         OrderState.EXPIRED}),
}

# Broker status string -> our state. Only DEFINITIVE states are mapped: 'new'/
# 'accepted'/'pending_new' occur while the order is freshly working (== SUBMITTED),
# and the fill/cancel/reject/expire endpoints. Unknown statuses map to None.
_BROKER_STATUS: dict[str, OrderState] = {
    "new": OrderState.SUBMITTED,
    "accepted": OrderState.SUBMITTED,
    "pending_new": OrderState.SUBMITTED,
    "partially_filled": OrderState.PARTIALLY_FILLED,
    "filled": OrderState.FILLED,
    "done_for_day": OrderState.EXPIRED,
    "canceled": OrderState.CANCELED,
    "cancelled": OrderState.CANCELED,
    "expired": OrderState.EXPIRED,
    "rejected": OrderState.REJECTED,
}

# Transient "in flux" broker states. They don't map to a definitive OrderState —
# collapsing them onto SUBMITTED would be an illegal backward move once an order is
# PARTIALLY_FILLED (spurious warnings + a stalled lifecycle). sync() leaves the order
# as-is when it sees one of these and waits for a definitive status.
_IGNORED_STATUS: frozenset[str] = frozenset({
    "accepted_for_bidding", "held", "pending_replace", "replaced",
    "pending_cancel", "suspended", "stopped",
})


def map_broker_status(status: str) -> OrderState | None:
    """Normalise a broker status string to an OrderState (None if unrecognised)."""
    return _BROKER_STATUS.get(str(status).strip().lower())


def is_legal(current: OrderState, to: OrderState) -> bool:
    return to in _LEGAL.get(current, frozenset())


@dataclass
class OrderRecord:
    id: int
    symbol: str
    side: str
    qty: float
    status: OrderState
    intended_price: float | None
    filled_qty: float
    avg_fill_price: float | None
    commission: float
    broker_order_id: str | None
    strategy: str | None


class OMS:
    """State-machine wrapper over the journal's `orders` / `order_events` tables."""

    def __init__(self, journal: TradeJournal) -> None:
        self.j = journal
        self.conn = journal.conn

    # --- write side -------------------------------------------------------
    def on_submit(
        self, *, symbol: str, side: str, qty: float, intended_price: float,
        broker_order_id: str, strategy: str | None = None,
        order_type: str = "market", client_order_id: str | None = None,
    ) -> int:
        """Record a just-placed order (NEW -> SUBMITTED). Returns the OMS order id."""
        now = datetime.now(UTC).isoformat(timespec="seconds")
        cur = self.conn.execute(
            """INSERT INTO orders
               (client_order_id, broker_order_id, symbol, side, qty, order_type,
                intended_price, strategy, status, filled_qty, avg_fill_price,
                commission, submitted_at, updated_at, filled_at)
               VALUES (?,?,?,?,?,?,?,?,?,0,NULL,0,?,?,NULL)""",
            (client_order_id, broker_order_id, symbol, side.lower(), float(qty),
             order_type, float(intended_price), strategy, OrderState.SUBMITTED.value,
             now, now),
        )
        oid = cur.lastrowid
        assert oid is not None
        self.conn.execute(
            "INSERT INTO order_events (order_id, ts, from_state, to_state, detail) "
            "VALUES (?,?,?,?,?)",
            (oid, now, OrderState.NEW.value, OrderState.SUBMITTED.value,
             f"submitted {side} {qty:g} {symbol} @ ~{intended_price:.4f} -> {broker_order_id}"),
        )
        self.conn.commit()
        log.info(f"[oms] order #{oid} SUBMITTED {side} {qty:g} {symbol} -> {broker_order_id}")
        return oid

    def transition(
        self, order_id: int, to: OrderState, *, filled_qty: float | None = None,
        avg_fill_price: float | None = None, commission: float | None = None,
        detail: str = "",
    ) -> bool:
        """Move an order to `to`, updating fill fields. Refuses illegal transitions.

        Returns True if a transition was applied. A no-op (same state) updates the
        fill fields (partial-fill progress) without logging a spurious event."""
        row = self.conn.execute(
            "SELECT status, filled_at FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row is None:
            log.error(f"[oms] transition on unknown order #{order_id}")
            return False
        current = OrderState(row[0])
        existing_filled_at = row[1]
        now = datetime.now(UTC).isoformat(timespec="seconds")

        # Same-state update (e.g. a growing partial fill): refresh fields, no event.
        if to == current:
            self._update_fill_fields(order_id, now, filled_qty, avg_fill_price, commission)
            return False

        if not is_legal(current, to):
            log.warning(f"[oms] refusing illegal transition #{order_id} {current.value} -> {to.value}")
            return False

        # Stamp when execution first happened — the moment any real fill lands, even if
        # it lands straight into a terminal state (e.g. filled-then-canceled remainder).
        # Only stamp once, so filled_at records the FIRST fill, not the latest poll.
        has_fill = filled_qty is not None and filled_qty > 0
        moved_to_fill = to in (OrderState.FILLED, OrderState.PARTIALLY_FILLED)
        filled_at = now if (not existing_filled_at and (moved_to_fill or has_fill)) else None
        self._update_fill_fields(order_id, now, filled_qty, avg_fill_price, commission,
                                 status=to, filled_at=filled_at)
        self.conn.execute(
            "INSERT INTO order_events (order_id, ts, from_state, to_state, detail) "
            "VALUES (?,?,?,?,?)",
            (order_id, now, current.value, to.value, detail),
        )
        self.conn.commit()
        log.info(f"[oms] order #{order_id} {current.value} -> {to.value}"
                 + (f" ({detail})" if detail else ""))
        return True

    def _update_fill_fields(
        self, order_id: int, now: str, filled_qty: float | None,
        avg_fill_price: float | None, commission: float | None,
        *, status: OrderState | None = None, filled_at: str | None = None,
    ) -> None:
        # Only touch the columns we were actually given (partial updates are common).
        updates: list[tuple[str, object]] = [("updated_at", now)]
        if status is not None:
            updates.append(("status", status.value))
        if filled_qty is not None:
            updates.append(("filled_qty", float(filled_qty)))
        if avg_fill_price is not None:
            updates.append(("avg_fill_price", float(avg_fill_price)))
        if commission is not None:
            updates.append(("commission", float(commission)))
        if filled_at is not None:
            updates.append(("filled_at", filled_at))
        clause = ", ".join(f"{col} = ?" for col, _ in updates)
        vals = [v for _, v in updates] + [order_id]
        self.conn.execute(f"UPDATE orders SET {clause} WHERE id = ?", vals)
        self.conn.commit()

    def sync(self, broker) -> int:
        """Poll the broker for every non-terminal order and advance its state.

        Idempotent and best-effort: a broker read that fails is logged and skipped so
        one bad order never stalls the rest. Returns how many orders changed state."""
        if not hasattr(broker, "order_status"):
            return 0
        changed = 0
        for rec in self.open_orders():
            if not rec.broker_order_id:
                continue
            try:
                st = broker.order_status(rec.broker_order_id)
            except Exception as exc:  # noqa: BLE001 - one order's failure must not stall sync
                log.warning(f"[oms] could not read status for #{rec.id} "
                            f"({rec.broker_order_id}): {exc}")
                continue
            if not st:
                continue
            raw = str(st.get("status", "")).strip().lower()
            if raw in _IGNORED_STATUS:
                continue                              # transient state — leave order as-is
            to = map_broker_status(raw)
            if to is None:
                log.warning(f"[oms] #{rec.id} unknown broker status {st.get('status')!r}")
                continue
            if self.transition(
                rec.id, to,
                filled_qty=st.get("filled_qty"),
                avg_fill_price=st.get("filled_avg_price"),
                commission=st.get("commission"),
                detail=f"broker status={st.get('status')}",
            ):
                changed += 1
        if changed:
            log.info(f"[oms] sync advanced {changed} order(s)")
        return changed

    # --- read side --------------------------------------------------------
    def open_orders(self) -> list[OrderRecord]:
        """Every order not yet in a terminal state."""
        placeholders = ",".join("?" for _ in _TERMINAL)
        rows = self.conn.execute(
            f"SELECT id, symbol, side, qty, status, intended_price, filled_qty, "
            f"avg_fill_price, commission, broker_order_id, strategy FROM orders "
            f"WHERE status NOT IN ({placeholders}) ORDER BY id",
            tuple(s.value for s in _TERMINAL),
        ).fetchall()
        return [self._to_record(r) for r in rows]

    def get(self, order_id: int) -> OrderRecord | None:
        row = self.conn.execute(
            "SELECT id, symbol, side, qty, status, intended_price, filled_qty, "
            "avg_fill_price, commission, broker_order_id, strategy FROM orders WHERE id = ?",
            (order_id,)).fetchone()
        return self._to_record(row) if row else None

    @staticmethod
    def _to_record(r) -> OrderRecord:
        return OrderRecord(
            id=r[0], symbol=r[1], side=r[2], qty=r[3], status=OrderState(r[4]),
            intended_price=r[5], filled_qty=r[6] or 0.0, avg_fill_price=r[7],
            commission=r[8] or 0.0, broker_order_id=r[9], strategy=r[10])
