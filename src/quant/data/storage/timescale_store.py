"""TimescaleDB-backed bar store — the scalable alternative to the parquet cache.

Same `BarStore` contract as `ParquetStore`, so callers don't change: flip
`STORAGE_BACKEND=timescale` in `.env` and `get_store()` returns this instead.

Why TimescaleDB: bars live in a single `bars` hypertable partitioned by time, so
intraday/tick volumes that would bloat a parquet-per-symbol layout stay fast to
query and append. Upserts make re-downloads idempotent.

The `psycopg` (v3) import is lazy: this module imports fine without the driver
installed (so the rest of the package — and the offline test suite — never needs
Postgres). Install it with the optional extra:  `pip install -e ".[timescale]"`.
Bring up a local TimescaleDB with `docker compose up -d timescaledb` (see
docs/DEPLOYMENT.md).
"""
from __future__ import annotations

import pandas as pd

from config import get_settings
from quant.data.storage.base import BarStore
from quant.utils import get_logger

log = get_logger(__name__)

_OHLCV = ["open", "high", "low", "close", "volume"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol     text             NOT NULL,
    timeframe  text             NOT NULL,
    ts         timestamptz      NOT NULL,
    open       double precision,
    high       double precision,
    low        double precision,
    close      double precision,
    volume     double precision,
    PRIMARY KEY (symbol, timeframe, ts)
);
SELECT create_hypertable('bars', 'ts', if_not_exists => TRUE);
"""


class TimescaleStore(BarStore):
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or get_settings().timescale_dsn
        self._ensure_schema()

    def _connect(self):
        try:
            import psycopg  # lazy — only needed when this backend is actually used
        except ModuleNotFoundError as exc:  # pragma: no cover - depends on optional extra
            raise RuntimeError(
                "TimescaleStore needs the 'psycopg' driver. Install the extra:\n"
                '  pip install -e ".[timescale]"'
            ) from exc
        return psycopg.connect(self.dsn)

    def _ensure_schema(self) -> None:
        """Create the hypertable if missing. Surfaces a clear error if the DB
        is unreachable or the timescaledb extension isn't enabled."""
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(_SCHEMA)
                conn.commit()
        except Exception as exc:  # pragma: no cover - needs a live DB
            raise RuntimeError(
                f"could not initialize TimescaleDB at {self.dsn!r}: "
                f"{type(exc).__name__}: {exc}\n"
                "  - is the database up?  docker compose up -d timescaledb\n"
                "  - does TIMESCALE_DSN in .env point at it?"
            ) from exc

    def save(self, symbol: str, timeframe: str, df: pd.DataFrame) -> str:
        """Upsert `df`'s bars (idempotent on the (symbol, timeframe, ts) key)."""
        rows = [
            (symbol, timeframe, ts.to_pydatetime(),
             *(float(r[c]) for c in _OHLCV))
            for ts, r in df.iterrows()
        ]
        with self._connect() as conn, conn.cursor() as cur:
            cur.executemany(
                """INSERT INTO bars (symbol, timeframe, ts, open, high, low, close, volume)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (symbol, timeframe, ts) DO UPDATE SET
                     open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low,
                     close=EXCLUDED.close, volume=EXCLUDED.volume""",
                rows,
            )
            conn.commit()
        log.info(f"timescale: upserted {len(rows)} bars for {symbol} {timeframe}")
        return f"timescale:bars[{symbol}/{timeframe}] {len(rows)} rows"

    def load(self, symbol: str, timeframe: str) -> pd.DataFrame | None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ts, open, high, low, close, volume FROM bars "
                "WHERE symbol=%s AND timeframe=%s ORDER BY ts",
                (symbol, timeframe),
            )
            rows = cur.fetchall()
        if not rows:
            return None
        df = pd.DataFrame(rows, columns=["ts", *_OHLCV]).set_index("ts")
        df.index = pd.DatetimeIndex(df.index, name="timestamp")  # tz-aware (UTC) from timestamptz
        return df[_OHLCV].astype(float)

    def exists(self, symbol: str, timeframe: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM bars WHERE symbol=%s AND timeframe=%s LIMIT 1",
                (symbol, timeframe),
            )
            return cur.fetchone() is not None
