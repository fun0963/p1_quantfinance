"""Daily operations report (breakdown M10.5) — the end-of-day 'what did the system
do today' summary: positions, cash/equity, today's decisions and orders, blocked
orders, and the reconciliation status. Rendered as plain text (logged and/or pushed
to Telegram). Read-only."""
from __future__ import annotations

from datetime import UTC, date, datetime

from quant.execution.base import Broker
from quant.execution.journal import TradeJournal
from quant.ops.reconcile import reconcile


def daily_report(broker: Broker, journal: TradeJournal, on: date | None = None) -> str:
    on = on or datetime.now(UTC).date()
    lines = [f"Daily report - {on.isoformat()}"]

    # --- account & positions ---
    positions = broker.get_positions()
    lines.append("positions: " + (", ".join(
        f"{p.symbol} {p.qty:g}@{p.avg_price:.2f}" for p in positions) or "none"))
    if hasattr(broker, "account_summary"):
        try:
            s = broker.account_summary()
            lines.append(f"equity: {s.get('equity')}  cash: {s.get('cash')}")
        except Exception:  # noqa: BLE001 - report is best-effort
            pass

    # --- today's live decisions ---
    df = journal.live_log(limit=500)
    today = df[df["logged_at"].astype(str).str.startswith(on.isoformat())] if not df.empty else df
    if today is None or today.empty:
        lines.append("decisions today: none")
    else:
        placed = today[today["order_id"].notna()]
        blocked = today[today["blocked"].notna()]
        lines.append(f"decisions today: {len(today)}  ({len(placed)} orders, {len(blocked)} blocked)")
        for _, r in placed.iterrows():
            lines.append(f"  - {r['action']} {r['qty']:g} {r['symbol']} @ {r['price']:.2f} -> {r['order_id']}")
        for _, r in blocked.head(5).iterrows():
            lines.append(f"  ! {r['symbol']}: {r['blocked']}")

    # --- reconciliation ---
    rep = reconcile(broker, journal)
    if not rep.issues:
        lines.append("reconcile: clean")
    else:
        lines.append(rep.summary() + ("" if rep.ok else "  <<< CRITICAL"))
        for i in rep.issues[:8]:
            lines.append(f"  {i.severity}: {i.detail}")

    return "\n".join(lines)
