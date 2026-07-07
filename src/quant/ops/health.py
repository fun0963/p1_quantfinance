"""System health — heartbeats and pipeline monitoring (breakdown M10.2 / M10.3).

An unattended trader fails silently: the scheduled job simply stops firing and no
one notices until a position is mismanaged for days. The defence is a heartbeat —
every run stamps a proof-of-life row, and a health check flags any component that
has gone quiet for longer than expected (a missed run) or last reported an error.

`record_heartbeat` is called at the end of each live run and scheduler tick;
`health_check` is what the daily report / `quant health` reads to answer "is the
system alive and did today's run actually happen?". Read/write only the heartbeats
table — no trading side effects.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from quant.execution.journal import TradeJournal
from quant.utils import get_logger

log = get_logger(__name__)


def record_heartbeat(
    journal: TradeJournal, component: str, status: str = "ok",
    detail: str = "", meta: dict | None = None,
) -> int:
    """Stamp a proof-of-life row for `component`. status: ok | warn | error."""
    ts = datetime.now(UTC).isoformat(timespec="seconds")
    cur = journal.conn.execute(
        "INSERT INTO heartbeats (ts, component, status, detail, meta) VALUES (?,?,?,?,?)",
        (ts, component, status, detail, json.dumps(meta or {})),
    )
    journal.conn.commit()
    assert cur.lastrowid is not None
    return cur.lastrowid


@dataclass
class ComponentHealth:
    component: str
    last_ts: str | None          # None = never seen
    age_minutes: float | None
    status: str                  # last reported status, or 'missing'
    stale: bool                  # silent longer than allowed, or never seen
    detail: str = ""

    @property
    def alive(self) -> bool:
        return not self.stale and self.status != "error"


@dataclass
class HealthReport:
    ok: bool
    components: list[ComponentHealth]
    checked_at: str
    max_silence_minutes: int
    problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        head = "health: OK" if self.ok else f"health: DEGRADED ({len(self.problems)} problem(s))"
        parts = []
        for c in self.components:
            if c.last_ts is None:
                parts.append(f"{c.component}=never")
            else:
                age = f"{c.age_minutes:.0f}m" if c.age_minutes is not None else "?"
                parts.append(f"{c.component}={c.status}@{age}ago")
        return head + " - " + ", ".join(parts) if parts else head


def _latest_by_component(journal: TradeJournal) -> dict[str, tuple[str, str, str]]:
    """component -> (last_ts, last_status, last_detail), newest row per component."""
    rows = journal.conn.execute(
        "SELECT component, ts, status, detail FROM heartbeats h "
        "WHERE id = (SELECT MAX(id) FROM heartbeats WHERE component = h.component)"
    ).fetchall()
    return {r[0]: (r[1], r[2], r[3] or "") for r in rows}


def health_check(
    journal: TradeJournal, *, now: datetime | None = None,
    max_silence_minutes: int = 1500, expect: tuple[str, ...] = (),
    clock_skew_tolerance_minutes: float = 5.0,
) -> HealthReport:
    """Assess component liveness. A problem (which flips `ok` to False) is any seen
    component that is silent too long, is stamped in the future (clock skew), or last
    reported 'error', plus any component in `expect` that has never reported at all.

    `expect` defaults to empty so a component that has simply never been used (e.g. the
    in-process scheduler on a manual-`quant live` setup) is not a false alarm — a
    component that ran before and then went quiet is still caught by the staleness
    check. max_silence_minutes defaults to 1500 (~25h) — one missed daily run trips it.
    A heartbeat dated more than clock_skew_tolerance_minutes in the future is treated as
    suspect (a negative age must never read as "fresh" and mask a dead component).
    """
    now = now or datetime.now(UTC)
    latest = _latest_by_component(journal)
    # Report on the union of expected and actually-seen components.
    names = list(dict.fromkeys([*expect, *latest.keys()]))

    components: list[ComponentHealth] = []
    problems: list[str] = []
    for name in names:
        if name not in latest:
            components.append(ComponentHealth(name, None, None, "missing", stale=True,
                                              detail="no heartbeat on record"))
            if name in expect:
                problems.append(f"{name}: never reported")
            continue
        ts_str, status, detail = latest[name]
        age_min = _age_minutes(ts_str, now)
        future = age_min is not None and age_min < -clock_skew_tolerance_minutes
        stale = age_min is None or age_min > max_silence_minutes or future
        ch = ComponentHealth(name, ts_str, age_min, status, stale=stale, detail=detail)
        components.append(ch)
        # A component that has reported before is a problem if it has since gone quiet
        # (missed run), is future-dated (clock skew), or last errored — regardless of
        # whether it's in `expect`.
        if stale:
            if age_min is None:
                problems.append(f"{name}: unparseable timestamp")
            elif future:
                problems.append(f"{name}: heartbeat {-age_min:.0f}m in the FUTURE (clock skew?)")
            else:
                problems.append(f"{name}: silent {age_min:.0f}m (> {max_silence_minutes}m)")
        elif status == "error":
            problems.append(f"{name}: last run errored - {detail}")

    return HealthReport(
        ok=not problems, components=components,
        checked_at=now.isoformat(timespec="seconds"),
        max_silence_minutes=max_silence_minutes, problems=problems,
    )


def _age_minutes(ts_str: str, now: datetime) -> float | None:
    try:
        ts = datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return (now - ts).total_seconds() / 60.0
