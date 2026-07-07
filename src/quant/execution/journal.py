"""Persistent trade journal (SQLite) — the audit trail.

Every paper (and later, live) session is recorded: a `sessions` row with its
config and headline metrics, plus every `fill` and every `blocked` order. This is
the auditable record you want before routing real money — answer "what did the
system do, when, and why was that order stopped?" long after the run.

Plain `sqlite3` (stdlib) — zero extra dependencies, one file under `data/`.
Swap the path for Postgres/Timescale later without changing callers.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from config import get_settings
from quant.execution.session import PaperSessionResult
from quant.utils import get_logger

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT NOT NULL,
    mode              TEXT NOT NULL,      -- 'paper' | 'live'
    symbol            TEXT NOT NULL,
    strategy          TEXT NOT NULL,
    params            TEXT,
    bars              INTEGER,
    period_start      TEXT,
    period_end        TEXT,
    init_cash         REAL,
    final_equity      REAL,
    total_return_pct  REAL,
    sharpe            REAL,
    max_drawdown_pct  REAL,
    num_fills         INTEGER,
    num_blocked       INTEGER,
    exit_reasons      TEXT
);
CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts       TEXT,
    order_id TEXT,
    symbol   TEXT,
    side     TEXT,
    qty      REAL,
    price    REAL
);
CREATE TABLE IF NOT EXISTS blocked (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    ts     TEXT,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS ix_fills_session ON fills(session_id);
CREATE INDEX IF NOT EXISTS ix_blocked_session ON blocked(session_id);

CREATE TABLE IF NOT EXISTS live_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    logged_at TEXT NOT NULL,        -- when the live step ran
    bar_ts    TEXT,                 -- timestamp of the evaluated bar
    symbol    TEXT,
    strategy  TEXT,
    action    TEXT,                 -- buy | sell | hold | flat
    qty       REAL,
    price     REAL,
    order_id  TEXT,
    blocked   TEXT,
    dry_run   INTEGER,
    reason    TEXT
);
-- Drift/reconcile filter live_log by (symbol, strategy); index it so those reads
-- don't scan the whole table (and so the newest-N slice is taken per symbol).
CREATE INDEX IF NOT EXISTS ix_live_log_symbol ON live_log(symbol, strategy, id);

-- OMS: the lifecycle of every REAL order the live runner places. `intended_price`
-- is the arrival/decision price (latest close when we decided) — the TCA benchmark
-- that `avg_fill_price` is measured against.
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT UNIQUE,     -- our idempotency id
    broker_order_id TEXT,            -- id returned by the broker
    symbol   TEXT NOT NULL,
    side     TEXT NOT NULL,          -- buy | sell
    qty      REAL NOT NULL,          -- intended quantity
    order_type TEXT,
    intended_price REAL,             -- arrival/decision price (TCA benchmark)
    strategy TEXT,
    status   TEXT NOT NULL,          -- OrderState: NEW|SUBMITTED|PARTIALLY_FILLED|FILLED|CANCELED|REJECTED|EXPIRED
    filled_qty REAL DEFAULT 0,
    avg_fill_price REAL,
    commission REAL DEFAULT 0,
    submitted_at TEXT,
    updated_at   TEXT,
    filled_at    TEXT
);
CREATE TABLE IF NOT EXISTS order_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    ts         TEXT NOT NULL,
    from_state TEXT,
    to_state   TEXT NOT NULL,
    detail     TEXT
);
CREATE INDEX IF NOT EXISTS ix_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS ix_order_events_order ON order_events(order_id);

-- Heartbeats: proof-of-life for each component (scheduler, live step). A missing
-- heartbeat = a job that didn't run; the daily health check flags the silence.
CREATE TABLE IF NOT EXISTS heartbeats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        TEXT NOT NULL,        -- when recorded (UTC)
    component TEXT NOT NULL,        -- scheduler | live | ...
    status    TEXT NOT NULL,        -- ok | warn | error
    detail    TEXT,
    meta      TEXT                  -- json blob (symbol, bar_ts, action, latency...)
);
CREATE INDEX IF NOT EXISTS ix_heartbeats_component ON heartbeats(component, id);
"""


class TradeJournal:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.path = Path(db_path) if db_path else get_settings().data_dir / "journal.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # WAL + a busy timeout so concurrent access (web + CLI + scheduler) doesn't
        # raise "database is locked" and drop an audit record of an order.
        self.conn = sqlite3.connect(str(self.path), timeout=30.0)
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 30000")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def record_session(
        self,
        result: PaperSessionResult,
        *,
        symbol: str,
        strategy: str,
        params: dict | None = None,
        init_cash: float,
        mode: str = "paper",
    ) -> int:
        """Persist a session and all its fills/blocks; return the new session id."""
        eq = result.equity_curve
        cur = self.conn.execute(
            """INSERT INTO sessions
               (created_at, mode, symbol, strategy, params, bars, period_start,
                period_end, init_cash, final_equity, total_return_pct, sharpe,
                max_drawdown_pct, num_fills, num_blocked, exit_reasons)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(UTC).isoformat(timespec="seconds"),
                mode, symbol, strategy, json.dumps(params or {}),
                len(eq), str(eq.index[0]) if len(eq) else None,
                str(eq.index[-1]) if len(eq) else None,
                init_cash, result.metrics.get("final_equity"),
                result.metrics.get("total_return_pct"), result.metrics.get("sharpe"),
                result.metrics.get("max_drawdown_pct"), len(result.fills),
                len(result.blocked), json.dumps(result.exit_reasons),
            ),
        )
        sid = cur.lastrowid
        assert sid is not None  # sqlite sets lastrowid after an INSERT

        self.conn.executemany(
            "INSERT INTO fills (session_id, ts, order_id, symbol, side, qty, price) "
            "VALUES (?,?,?,?,?,?,?)",
            [(sid, str(f.ts), f.order_id, f.symbol, f.side.value, f.qty, f.price)
             for f in result.fills],
        )
        self.conn.executemany(
            "INSERT INTO blocked (session_id, ts, reason) VALUES (?,?,?)",
            [(sid, str(ts), reason) for ts, reason in result.blocked],
        )
        self.conn.commit()
        log.info(f"journal: recorded session #{sid} ({symbol}/{strategy}, "
                 f"{len(result.fills)} fills) -> {self.path}")
        return sid

    def record_live_decision(self, decision, *, strategy: str) -> int:
        """Persist a single live-runner decision; return its row id."""
        cur = self.conn.execute(
            """INSERT INTO live_log
               (logged_at, bar_ts, symbol, strategy, action, qty, price,
                order_id, blocked, dry_run, reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(UTC).isoformat(timespec="seconds"),
                str(decision.ts), decision.symbol, strategy, decision.action,
                decision.qty, decision.price, decision.order_id, decision.blocked,
                int(decision.dry_run), decision.reason,
            ),
        )
        self.conn.commit()
        assert cur.lastrowid is not None  # sqlite sets lastrowid after an INSERT
        return cur.lastrowid

    # --- read side ---------------------------------------------------------
    def live_log(self, limit: int = 30, *, symbol: str | None = None,
                 strategy: str | None = None) -> pd.DataFrame:
        """Recent live-runner decisions, newest first. Optional symbol/strategy
        filters are pushed into SQL (indexed) so callers like drift don't pull
        the whole table and slice in pandas — which could also drop older bars
        for the target symbol when other symbols dominate the recent rows."""
        where: list[str] = []
        params: list[str | int] = []
        if symbol:
            where.append("symbol = ?")
            params.append(symbol)
        if strategy:
            where.append("strategy = ?")
            params.append(strategy)
        clause = f"WHERE {' AND '.join(where)} " if where else ""
        params.append(limit)
        return pd.read_sql_query(
            "SELECT id, logged_at, bar_ts, symbol, strategy, action, qty, price, "
            f"order_id, blocked, dry_run FROM live_log {clause}ORDER BY id DESC LIMIT ?",
            self.conn, params=tuple(params),
        )

    def sessions(self, limit: int = 20) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT id, created_at, mode, symbol, strategy, total_return_pct, "
            "sharpe, max_drawdown_pct, num_fills, num_blocked "
            "FROM sessions ORDER BY id DESC LIMIT ?",
            self.conn, params=(limit,),
        )

    def fills(self, session_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT ts, order_id, symbol, side, qty, price FROM fills "
            "WHERE session_id = ? ORDER BY id",
            self.conn, params=(session_id,),
        )

    def blocked(self, session_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT ts, reason FROM blocked WHERE session_id = ? ORDER BY id",
            self.conn, params=(session_id,),
        )

    def orders(self, limit: int = 100) -> pd.DataFrame:
        """OMS order records (newest first). Used by TCA, the daily report, and the UI."""
        return pd.read_sql_query(
            "SELECT id, client_order_id, broker_order_id, symbol, side, qty, order_type, "
            "intended_price, strategy, status, filled_qty, avg_fill_price, commission, "
            "submitted_at, updated_at, filled_at FROM orders ORDER BY id DESC LIMIT ?",
            self.conn, params=(limit,),
        )

    def order_events(self, order_id: int) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT ts, from_state, to_state, detail FROM order_events "
            "WHERE order_id = ? ORDER BY id",
            self.conn, params=(order_id,),
        )

    def heartbeats(self, limit: int = 50) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT id, ts, component, status, detail, meta FROM heartbeats "
            "ORDER BY id DESC LIMIT ?",
            self.conn, params=(limit,),
        )
