"""Read-only query layer shared by `quant ... --json` and the MCP server.

Every function returns PLAIN, json-serializable data (numbers stay numbers,
NaN -> None, timestamps -> ISO strings) so both surfaces emit identical shapes
- one source of truth, no drift between the CLI contract and MCP tools.

HARD RULE (same family as "spec can never contain execute" and the read-only
web dashboard): nothing in this module places, cancels or modifies orders, or
mutates broker state. Broker access is limited to read calls (account summary,
positions) and the read-only reconcile comparison.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def json_default(o: Any) -> Any:
    """Serialize what stdlib json can't: numpy scalars stay NUMBERS (an agent
    doing math on "sharpe" must not get a string); everything else -> str
    (Timestamps, Paths, dates, enums)."""
    import numpy as np

    if isinstance(o, np.bool_):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


def plain(data: Any) -> Any:
    """Round-trip through json so callers always hold plain dict/list/str/num."""
    return json.loads(json.dumps(data, ensure_ascii=True, default=json_default))


def df_records(df) -> list:
    """DataFrame -> plain-JSON records (NaN -> null, timestamps -> ISO strings)."""
    return json.loads(df.to_json(orient="records", date_format="iso"))


# --- monitoring --------------------------------------------------------------
def health_snapshot(max_silence_minutes: int = 1500) -> dict:
    """Heartbeat health incl. per-component ages; key "ok" carries the verdict."""
    from dataclasses import asdict

    from quant.execution import TradeJournal
    from quant.ops.health import health_check

    with TradeJournal() as tj:
        rep = health_check(tj, max_silence_minutes=max_silence_minutes)
    return plain({
        "ok": rep.ok, "summary": rep.summary(), "checked_at": rep.checked_at,
        "max_silence_minutes": rep.max_silence_minutes, "problems": rep.problems,
        "components": [asdict(c) for c in rep.components],
    })


def live_decisions(limit: int = 20) -> dict:
    """Most recent live-runner decisions (newest first) from the journal."""
    from quant.execution import TradeJournal

    with TradeJournal() as tj:
        rows = tj.live_log(limit=limit)
        return plain({"db": str(tj.path), "records": df_records(rows)})


def orders_snapshot(limit: int = 20) -> dict:
    """Tracked orders and their OMS state (newest first). No broker sync here -
    syncing advances journal state, which a read-only surface must not do."""
    from quant.execution import TradeJournal

    cols = ["id", "symbol", "side", "qty", "status", "intended_price",
            "avg_fill_price", "filled_qty", "broker_order_id"]
    with TradeJournal() as tj:
        rows = tj.orders(limit=limit)
        avail = [c for c in cols if c in rows.columns]
        return plain({"db": str(tj.path),
                      "records": df_records(rows[avail]) if not rows.empty else []})


def tca_snapshot(strategy: str | None = None, limit: int = 1000) -> dict:
    """Transaction-cost analysis rollup + per-order slippage records."""
    from quant.execution import TradeJournal
    from quant.ops.tca import tca_report

    with TradeJournal() as tj:
        rep = tca_report(tj, strategy=strategy or None, limit=limit)
    cols = ["symbol", "side", "filled_qty", "intended_price", "avg_fill_price",
            "slippage_bps", "total_cost_usd"]
    avail = [c for c in cols if c in rep.per_order.columns]
    stats = {k: getattr(rep, k) for k in (
        "n_orders", "n_filled", "fill_rate", "avg_slippage_bps",
        "median_slippage_bps", "worst_slippage_bps", "total_slippage_usd",
        "total_commission_usd", "total_cost_usd", "total_notional_usd",
        "cost_bps_of_notional")}
    return plain({"summary": rep.summary(), **stats,
                  "per_order": df_records(rep.per_order[avail]) if rep.n_filled else []})


def _default_broker(name: str):
    """Construct a broker CLIENT for read-only queries (positions/summary).
    Nothing in this module ever calls its mutating methods."""
    if name == "paper":
        from quant.execution import PaperBroker

        return PaperBroker()
    from quant.execution.alpaca_broker import AlpacaBroker

    return AlpacaBroker()


def status_snapshot(broker: str = "alpaca", offline: bool = False,
                    max_silence_minutes: int = 1500, limit: int = 5,
                    broker_factory=None) -> tuple[dict, bool]:
    """Aggregated system snapshot; returns (sections, overall_ok).

    Sections degrade INDEPENDENTLY: a broker outage marks that section
    {"error": ...} and flips overall_ok, but never hides local state.
    Specs are listed from config only (no evaluation) to keep this instant.
    `broker_factory` is injectable (the CLI passes its own; tests fake it).
    """
    from dataclasses import asdict

    from quant.execution import TradeJournal
    from quant.ops.reconcile import reconcile as run_reconcile
    from quant.strategies.spec import load_specs

    checked_at = datetime.now(UTC).isoformat(timespec="seconds")
    sections: dict = {"checked_at": checked_at, "broker_name": broker}
    checks: list[bool] = []

    if offline:
        sections["broker"] = {"skipped": "offline"}
    else:
        try:
            factory = broker_factory or _default_broker
            brk = factory(broker)
            summary = brk.account_summary() if hasattr(brk, "account_summary") else {}
            positions = brk.get_positions()
            with TradeJournal() as tj:
                rec = run_reconcile(brk, tj)
            sections["broker"] = {
                "account": summary,
                "positions": [{"symbol": p.symbol, "qty": p.qty, "avg_price": p.avg_price}
                              for p in positions],
                "reconcile": {"ok": rec.ok, "summary": rec.summary(),
                              "issues": [asdict(i) for i in rec.issues]},
            }
            checks.append(rec.ok)
        except Exception as exc:  # noqa: BLE001 - degrade this section, keep the rest
            sections["broker"] = {"error": f"{type(exc).__name__}: {exc}"}
            checks.append(False)

    try:
        h = health_snapshot(max_silence_minutes=max_silence_minutes)
        sections["health"] = h
        checks.append(bool(h["ok"]))
    except Exception as exc:  # noqa: BLE001
        sections["health"] = {"error": f"{type(exc).__name__}: {exc}"}
        checks.append(False)

    try:
        sections["recent_decisions"] = live_decisions(limit=limit)["records"]
        cols = ["id", "symbol", "side", "qty", "status", "avg_fill_price", "broker_order_id"]
        orders = orders_snapshot(limit=limit)["records"]
        sections["recent_orders"] = [{k: r.get(k) for k in cols if k in r} for r in orders]
        t = tca_snapshot()
        sections["tca"] = {k: t[k] for k in ("summary", "n_filled", "avg_slippage_bps",
                                             "total_cost_usd")}
    except Exception as exc:  # noqa: BLE001
        sections["journal_error"] = f"{type(exc).__name__}: {exc}"
        checks.append(False)

    try:
        sections["specs"] = [{"name": sp.name, "symbol": sp.symbol, "strategy": sp.strategy,
                              "timeframe": sp.timeframe, "state": sp.state}
                             for sp in load_specs(None).values()]
    except Exception as exc:  # noqa: BLE001
        sections["specs"] = {"error": f"{type(exc).__name__}: {exc}"}
        checks.append(False)

    return plain(sections), (all(checks) if checks else True)


# --- research ----------------------------------------------------------------
def experiments_list(strategy: str | None = None, symbol: str | None = None,
                     limit: int = 20) -> dict:
    """Recent logged backtest experiments (the anti-overfitting research log)."""
    from quant.research import ExperimentStore

    with ExperimentStore() as store:
        df = store.recent(limit=limit, strategy=strategy or None, symbol=symbol or None)
    return plain({"records": df_records(df)})


def experiment_get(experiment_id: int) -> dict:
    """Full record for one experiment id, or {"error": ...} if unknown."""
    from quant.research import ExperimentStore

    with ExperimentStore() as store:
        rec = store.get(experiment_id)
    if rec is None:
        return {"error": f"no experiment #{experiment_id}"}
    return plain({"record": rec})


def notes_list(status: str | None = None, notes_dir: str | None = None) -> dict:
    """Knowledge-base notes index, newest first."""
    from quant.research import list_notes

    notes = list_notes(notes_dir or None, status=status or None)
    return plain({"records": [
        {"title": n.title, "status": n.status, "created": n.created,
         "strategy": n.strategy or None, "symbols": n.symbols,
         "experiments": n.experiments, "path": str(n.path)}
        for n in notes]})


def note_read(filename: str, notes_dir: str | None = None) -> dict:
    """One note's frontmatter + full body. Only the BASENAME of `filename` is
    used and it must resolve inside the notes directory - no path traversal."""
    from quant.research.notes import DEFAULT_NOTES_DIR, parse_note

    base = Path(notes_dir) if notes_dir else DEFAULT_NOTES_DIR
    p = (base / Path(filename).name).resolve()
    if base.resolve() not in p.parents or not p.exists():
        return {"error": f"no note named {Path(filename).name!r}"}
    n = parse_note(p)
    return plain({"title": n.title, "status": n.status, "created": n.created,
                  "strategy": n.strategy or None, "symbols": n.symbols,
                  "experiments": n.experiments, "path": str(n.path), "body": n.body})


def specs_list() -> dict:
    """Configured strategy specs (identity/params/risk/lifecycle/state) from
    configs/strategies.json. Listing only - lifecycle verdicts are computed by
    `quant lifecycle`, not here."""
    from dataclasses import asdict

    from quant.strategies.spec import load_specs

    return plain({"records": [asdict(sp) for sp in load_specs(None).values()]})
