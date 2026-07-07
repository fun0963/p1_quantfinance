"""Tests for heartbeats and the missed-run health check."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from quant.execution.journal import TradeJournal
from quant.ops.health import health_check, record_heartbeat


def test_fresh_heartbeat_is_healthy(tmp_path):
    with TradeJournal(db_path=tmp_path / "j.db") as tj:
        record_heartbeat(tj, "scheduler", "ok", "ran SPY")
        rep = health_check(tj, expect=("scheduler",))
    assert rep.ok
    sched = next(c for c in rep.components if c.component == "scheduler")
    assert sched.status == "ok" and not sched.stale


def test_missing_expected_component_is_degraded(tmp_path):
    with TradeJournal(db_path=tmp_path / "j.db") as tj:
        rep = health_check(tj, expect=("scheduler",))       # never recorded
    assert not rep.ok
    sched = next(c for c in rep.components if c.component == "scheduler")
    assert sched.status == "missing" and sched.stale
    assert any("never reported" in p for p in rep.problems)


def test_silent_component_goes_stale(tmp_path):
    with TradeJournal(db_path=tmp_path / "j.db") as tj:
        record_heartbeat(tj, "scheduler", "ok")
        # Look at it from 2000 minutes in the future — well past the 1500m threshold.
        future = datetime.now(UTC) + timedelta(minutes=2000)
        rep = health_check(tj, now=future, expect=("scheduler",))
    assert not rep.ok
    assert any("silent" in p for p in rep.problems)


def test_error_status_is_a_problem(tmp_path):
    with TradeJournal(db_path=tmp_path / "j.db") as tj:
        record_heartbeat(tj, "live", "error", "boom")
        rep = health_check(tj, expect=())          # not even expected, but error still flags
    assert not rep.ok
    assert any("errored" in p for p in rep.problems)


def test_future_dated_heartbeat_is_not_treated_as_fresh(tmp_path):
    """Clock skew can stamp a heartbeat in the future -> negative age. It must not
    silently read as 'fresh' and mask a dead component."""
    with TradeJournal(db_path=tmp_path / "j.db") as tj:
        record_heartbeat(tj, "scheduler", "ok")
        # Evaluate from 30 minutes BEFORE the heartbeat (as if the writer clock ran ahead).
        past = datetime.now(UTC) - timedelta(minutes=30)
        rep = health_check(tj, now=past, expect=("scheduler",))
    assert not rep.ok
    assert any("FUTURE" in p for p in rep.problems)
    sched = next(c for c in rep.components if c.component == "scheduler")
    assert sched.stale


def test_latest_heartbeat_wins(tmp_path):
    with TradeJournal(db_path=tmp_path / "j.db") as tj:
        record_heartbeat(tj, "live", "error", "old failure")
        record_heartbeat(tj, "live", "ok", "recovered")
        rep = health_check(tj, expect=("live",))
    live = next(c for c in rep.components if c.component == "live")
    assert live.status == "ok" and rep.ok        # newest row is the ok one
