"""Read-only MCP server: monitoring & research queries for AI agents.

Exposes the same payloads as `quant ... --json` (both surfaces share
`quant.readapi`) over the Model Context Protocol, stdio transport.

HARD RULE - same family as "a spec can never contain execute" and the
read-only web dashboard: there are deliberately NO trading actions here.
No tool places, cancels or modifies orders, no broker sync, no writes.
Order placement stays a human CLI act (`quant live --execute`).
tests/test_mcp_server.py pins both the tool registry and this invariant.

Run:      quant mcp            (or: python -m quant.mcp_server)
Register: .mcp.json at the repo root (Claude Code picks it up per-project).
"""
from __future__ import annotations

from collections.abc import Callable

from mcp.server.fastmcp import FastMCP

from quant import readapi

mcp = FastMCP(
    "quant-readonly",
    instructions=(
        "Read-only monitoring and research tools for the quant trading system. "
        "There are deliberately NO trading actions here; placing orders is a "
        "human CLI act. Start with get_status for the big picture."
    ),
)


def get_status(offline: bool = False, broker: str = "alpaca") -> dict:
    """Aggregated system snapshot: broker account + positions + reconcile,
    heartbeat health, recent live decisions, recent orders, TCA rollup and
    configured specs. Sections degrade independently (a broker outage never
    hides local state); offline=True skips broker calls entirely."""
    sections, ok = readapi.status_snapshot(broker=broker, offline=offline)
    return {"ok": ok, **sections}


def get_health(max_silence_minutes: int = 1500) -> dict:
    """Per-component heartbeat health (live runner, scheduler...). A component
    silent longer than max_silence_minutes is flagged stale."""
    return readapi.health_snapshot(max_silence_minutes=max_silence_minutes)


def get_live_decisions(limit: int = 20) -> dict:
    """Most recent live-runner decisions (newest first): bar, action, qty,
    order id, risk-gate blocks. The audit trail of what the system decided."""
    return readapi.live_decisions(limit=limit)


def get_orders(limit: int = 20) -> dict:
    """Tracked orders with their OMS state (newest first). Read-only view -
    it does NOT poll the broker; run `quant oms --sync` in the CLI for that."""
    return readapi.orders_snapshot(limit=limit)


def get_tca(strategy: str = "", limit: int = 1000) -> dict:
    """Transaction-cost analysis: fill rate, avg/median/worst slippage bps,
    total cost, plus per-order records. Optionally filter to one strategy."""
    return readapi.tca_snapshot(strategy=strategy or None, limit=limit)


def list_experiments(strategy: str = "", symbol: str = "", limit: int = 20) -> dict:
    """Recent backtest experiments (git hash, params, data window, cost,
    metrics) - the anti-overfitting research log."""
    return readapi.experiments_list(strategy=strategy or None,
                                    symbol=symbol or None, limit=limit)


def get_experiment(experiment_id: int) -> dict:
    """Full record for one experiment id (params/metrics decoded)."""
    return readapi.experiment_get(experiment_id)


def list_research_notes(status: str = "") -> dict:
    """Knowledge-base note index (newest first). status filter:
    idea | testing | adopted | rejected. Check rejected notes before
    proposing a new strategy idea - failed ideas are worth the most."""
    return readapi.notes_list(status=status or None)


def read_research_note(filename: str) -> dict:
    """One research note's frontmatter + full Markdown body. Pass the filename
    from list_research_notes (basename only; paths cannot escape the notes dir)."""
    return readapi.note_read(filename)


def list_specs() -> dict:
    """Configured strategy specs from configs/strategies.json: identity,
    params, risk limits, lifecycle rules, state. Listing only - lifecycle
    verdicts are computed by `quant lifecycle --all` in the CLI."""
    return readapi.specs_list()


# name -> fn; pinned by tests/test_mcp_server.py (any addition must consciously
# edit that test and MUST remain a pure read).
TOOLS: dict[str, Callable[..., dict]] = {
    "get_status": get_status,
    "get_health": get_health,
    "get_live_decisions": get_live_decisions,
    "get_orders": get_orders,
    "get_tca": get_tca,
    "list_experiments": list_experiments,
    "get_experiment": get_experiment,
    "list_research_notes": list_research_notes,
    "read_research_note": read_research_note,
    "list_specs": list_specs,
}
for _name, _fn in TOOLS.items():
    mcp.add_tool(_fn, name=_name)


def main() -> None:
    """Serve over stdio (blocking) - the standard transport for local clients."""
    mcp.run()


if __name__ == "__main__":
    main()
