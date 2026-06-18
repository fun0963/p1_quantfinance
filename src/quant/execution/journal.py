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
"""


class TradeJournal:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.path = Path(db_path) if db_path else get_settings().data_dir / "journal.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path))
        self.conn.execute("PRAGMA foreign_keys = ON")
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
    def live_log(self, limit: int = 30) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT id, logged_at, bar_ts, symbol, strategy, action, qty, price, "
            "order_id, blocked, dry_run FROM live_log ORDER BY id DESC LIMIT ?",
            self.conn, params=(limit,),
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
