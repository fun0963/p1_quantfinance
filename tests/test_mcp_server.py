"""Read-only MCP server: tool registry is pinned, tools run offline, and the
read-only invariant is enforced structurally (AST scan - no mutating broker
calls can appear in the server or the shared readapi layer).
"""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from quant import mcp_server, readapi  # noqa: E402

EXPECTED_TOOLS = {
    "get_status", "get_health", "get_live_decisions", "get_orders", "get_tca",
    "list_experiments", "get_experiment", "list_research_notes",
    "read_research_note", "list_specs",
}

# Identifiers that would mean this surface stopped being read-only.
FORBIDDEN_CALLS = {"submit_order", "place_order", "cancel_order", "close_position",
                   "protect_position", "sync", "record_session", "record_order"}


def _tmp_journal(monkeypatch, tmp_path):
    import quant.execution as ex
    real = ex.TradeJournal
    monkeypatch.setattr(ex, "TradeJournal", lambda *a, **k: real(tmp_path / "j.db"))


def test_tool_registry_is_pinned():
    """Adding a tool must be a conscious act: edit this set AND stay read-only."""
    assert set(mcp_server.TOOLS) == EXPECTED_TOOLS


def test_no_mutating_calls_in_server_or_readapi():
    """AST scan (not substring - docstrings may mention `--execute` etc.):
    neither module may CALL any order-mutating / journal-writing method."""
    for mod in (mcp_server, readapi):
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                fn = node.func
                name = fn.attr if isinstance(fn, ast.Attribute) else (
                    fn.id if isinstance(fn, ast.Name) else "")
                assert name not in FORBIDDEN_CALLS, f"{mod.__name__} calls {name}()"


def test_tools_run_offline_and_are_json_serializable(monkeypatch, tmp_path):
    _tmp_journal(monkeypatch, tmp_path)

    out = mcp_server.TOOLS["get_status"](offline=True)
    assert out["broker"] == {"skipped": "offline"} and out["ok"] is True

    health = mcp_server.TOOLS["get_health"]()
    assert health["ok"] is True                       # empty journal: nothing stale

    assert mcp_server.TOOLS["get_live_decisions"]()["records"] == []
    assert mcp_server.TOOLS["get_orders"]()["records"] == []
    assert mcp_server.TOOLS["get_tca"]()["n_filled"] == 0

    specs = mcp_server.TOOLS["list_specs"]()
    assert {s["name"] for s in specs["records"]} >= {"spy_momentum"}
    assert all("execute" not in s for s in specs["records"])   # the old iron rule

    for payload in (out, health, specs):
        json.dumps(payload)                            # plain data, no numpy leftovers


def test_note_tools_round_trip_and_block_traversal():
    notes = mcp_server.TOOLS["list_research_notes"]()
    assert notes["records"], "repo ships research notes"
    first = notes["records"][0]["path"]
    body = mcp_server.TOOLS["read_research_note"](first)
    assert body["title"] and body["body"].strip()

    for evil in ("../../.env", "..\\..\\config\\settings.py", "/etc/passwd"):
        assert "error" in mcp_server.TOOLS["read_research_note"](evil)


def test_experiment_tools(monkeypatch, tmp_path):
    import quant.research as research
    real = research.ExperimentStore
    monkeypatch.setattr(research, "ExperimentStore",
                        lambda *a, **k: real(db_path=tmp_path / "e.db"))
    assert mcp_server.TOOLS["list_experiments"]()["records"] == []
    assert "error" in mcp_server.TOOLS["get_experiment"](999_999)


async def test_protocol_handshake_lists_and_calls_tools(monkeypatch, tmp_path):
    """Gold check: speak REAL MCP (in-memory transport), not just call functions."""
    memory = pytest.importorskip("mcp.shared.memory")
    _tmp_journal(monkeypatch, tmp_path)
    low = getattr(mcp_server.mcp, "_mcp_server", None)
    if low is None:
        pytest.skip("fastmcp internals changed; direct-call tests still cover tools")
    async with memory.create_connected_server_and_client_session(low) as client:
        listed = await client.list_tools()
        assert {t.name for t in listed.tools} == EXPECTED_TOOLS
        res = await client.call_tool("get_health", {})
        assert not res.isError
        doc = json.loads(res.content[0].text)
        assert doc["ok"] is True
