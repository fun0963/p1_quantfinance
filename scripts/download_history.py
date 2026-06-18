"""One-off helper: bulk-download a watchlist into the parquet store.

Usage:
    python scripts/download_history.py
Edit SYMBOLS / START below. For ad-hoc single downloads, prefer `quant download`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from quant.data.feeds.yfinance_feed import YFinanceFeed
from quant.data.storage import ParquetStore
from quant.utils import setup_logging, get_logger

SYMBOLS = ["SPY", "QQQ", "AAPL"]
START = datetime(2020, 1, 1, tzinfo=timezone.utc)
TIMEFRAME = "1d"


def main() -> None:
    setup_logging()
    log = get_logger(__name__)
    feed, store = YFinanceFeed(), ParquetStore()
    for sym in SYMBOLS:
        df = feed.get_history(sym, start=START, timeframe=TIMEFRAME)
        store.save(sym, TIMEFRAME, df)
        log.info(f"{sym}: saved {len(df)} bars")


if __name__ == "__main__":
    main()
