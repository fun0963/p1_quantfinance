"""Experiment record system — every backtest run, logged and queryable.

"An experiment nobody recorded didn't happen." Sweeping hundreds of parameter
combos and keeping only the winner is exactly how a backtest overfits; a durable
log of *what was run* — code version, params, data window, cost assumptions and
results — turns iteration into something auditable and comparable after the fact,
and is the institutional defense against p-hacking your own research.

SQLite (stdlib), one file under `data/experiments.db` — same rationale as the
trade journal. Kept in a `research` layer that only depends on config/utils, so
it does not couple to the backtest engines (records are duck-typed in).
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from config import get_settings
from config.settings import ROOT_DIR
from quant.utils import get_logger

log = get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS experiments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at    TEXT NOT NULL,        -- when the experiment was recorded (UTC)
    git_hash  TEXT,                 -- short commit the code was on
    git_dirty INTEGER,              -- 1 = uncommitted changes (result not reproducible from HEAD)
    kind      TEXT NOT NULL,        -- backtest | sweep | walkforward
    symbol    TEXT,
    strategy  TEXT,
    params    TEXT,                 -- json: the strategy params
    start     TEXT,                 -- requested history start
    timeframe TEXT,
    engine    TEXT,
    fees_bps      REAL,             -- cost model, for apples-to-apples comparison
    slippage_bps  REAL,
    data_bars   INTEGER,            -- actual data window the result came from
    data_start  TEXT,
    data_end    TEXT,
    total_return_pct REAL,          -- headline metrics promoted to columns for querying
    sharpe           REAL,
    max_drawdown_pct REAL,
    num_trades       INTEGER,
    metrics   TEXT,                 -- full metrics dict (json)
    notes     TEXT
);
CREATE INDEX IF NOT EXISTS ix_experiments_strategy ON experiments(strategy, symbol, id);
"""


def git_revision() -> tuple[str, bool]:
    """(short commit hash, dirty?) of the project tree, or ('unknown', False) if
    git isn't available / this isn't a repo. `dirty` flags uncommitted changes so
    a result that can't be reproduced from a clean HEAD is visibly marked."""
    try:
        rev = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT_DIR,
                             capture_output=True, text=True, timeout=5)
        if rev.returncode != 0:
            return ("unknown", False)
        status = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT_DIR,
                                capture_output=True, text=True, timeout=5)
        return (rev.stdout.strip(), bool(status.stdout.strip()))
    except Exception as exc:  # noqa: BLE001 - git absent / not a repo: metadata only, never fail a run
        log.debug(f"git_revision failed: {exc}")
        return ("unknown", False)


class ExperimentStore:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self.path = Path(db_path) if db_path else get_settings().data_dir / "experiments.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30.0)
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def record(
        self,
        *,
        kind: str,
        symbol: str,
        strategy: str,
        params: dict | None,
        start: str,
        timeframe: str,
        engine: str,
        fees_bps: float,
        slippage_bps: float,
        data_bars: int,
        data_start: str,
        data_end: str,
        metrics: dict,
        notes: str = "",
        git: tuple[str, bool] | None = None,
    ) -> int:
        """Persist one experiment; return its id. `git` is injectable for tests."""
        gh, dirty = git if git is not None else git_revision()
        cur = self.conn.execute(
            """INSERT INTO experiments
               (run_at, git_hash, git_dirty, kind, symbol, strategy, params, start,
                timeframe, engine, fees_bps, slippage_bps, data_bars, data_start,
                data_end, total_return_pct, sharpe, max_drawdown_pct, num_trades,
                metrics, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                datetime.now(UTC).isoformat(timespec="seconds"), gh, int(dirty), kind,
                symbol, strategy, json.dumps(params or {}), start, timeframe, engine,
                fees_bps, slippage_bps, data_bars, data_start, data_end,
                metrics.get("total_return_pct"), metrics.get("sharpe"),
                metrics.get("max_drawdown_pct"), metrics.get("num_trades"),
                json.dumps(metrics), notes,
            ),
        )
        self.conn.commit()
        assert cur.lastrowid is not None  # sqlite sets lastrowid after an INSERT
        log.info(f"experiment #{cur.lastrowid} logged ({kind} {symbol}/{strategy}, "
                 f"{engine}) -> {self.path}")
        return cur.lastrowid

    # --- read side ---------------------------------------------------------
    def recent(self, limit: int = 20, *, strategy: str | None = None,
               symbol: str | None = None) -> pd.DataFrame:
        """Recent experiments (newest first), optionally filtered by strategy/symbol."""
        where: list[str] = []
        params: list[str | int] = []
        if strategy:
            where.append("strategy = ?")
            params.append(strategy)
        if symbol:
            where.append("symbol = ?")
            params.append(symbol)
        clause = f"WHERE {' AND '.join(where)} " if where else ""
        params.append(limit)
        return pd.read_sql_query(
            "SELECT id, run_at, git_hash, git_dirty, kind, symbol, strategy, engine, "
            "fees_bps, slippage_bps, total_return_pct, sharpe, max_drawdown_pct, num_trades "
            f"FROM experiments {clause}ORDER BY id DESC LIMIT ?",
            self.conn, params=tuple(params),
        )

    def get(self, exp_id: int) -> dict | None:
        """Full record for one experiment, with params/metrics decoded from json."""
        cur = self.conn.execute("SELECT * FROM experiments WHERE id = ?", (exp_id,))
        row = cur.fetchone()
        if row is None:
            return None
        rec = dict(zip([c[0] for c in cur.description], row))
        rec["params"] = json.loads(rec["params"] or "{}")
        rec["metrics"] = json.loads(rec["metrics"] or "{}")
        rec["git_dirty"] = bool(rec["git_dirty"])
        return rec


def log_backtest(store: ExperimentStore, result, *, symbol: str, strategy: str,
                 params: dict, start: str, timeframe: str, cost, data,
                 notes: str = "") -> int:
    """Record a BacktestResult as an experiment, pulling the data window from the
    frame and the cost assumptions from a CostModel. `result`/`cost`/`data` are
    duck-typed so this stays decoupled from the backtest layer."""
    return store.record(
        kind="backtest", symbol=symbol, strategy=strategy, params=params, start=start,
        timeframe=timeframe, engine=getattr(result, "engine", ""),
        fees_bps=round(cost.fees * 1e4, 4), slippage_bps=round(cost.slippage * 1e4, 4),
        data_bars=int(len(data)), data_start=str(data.index[0].date()),
        data_end=str(data.index[-1].date()), metrics=result.metrics, notes=notes,
    )
