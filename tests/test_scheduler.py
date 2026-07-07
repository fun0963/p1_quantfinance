"""Tests for the schedulable unit of work (live_and_journal) — offline, paper broker."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.execution.journal import TradeJournal
from quant.execution.scheduler import LiveConfig, live_and_journal


def _uptrend(n=200, seed=1):
    # A steady uptrend so a momentum strategy's target state is LONG at the last bar.
    idx = pd.date_range("2023-01-01", periods=n, freq="D", tz="UTC")
    close = pd.Series(100 + np.arange(n) * 0.5, index=idx)
    return pd.DataFrame(
        {"open": close, "high": close * 1.001, "low": close * 0.999,
         "close": close, "volume": 1e6}, index=idx,
    )


def test_live_and_journal_records_decision(tmp_path):
    cfg = LiveConfig(symbol="SPY", strategy="momentum", params={"lookback": 50},
                     broker="paper", max_bar_age_days=100_000)  # off: synthetic 2023 data
    with TradeJournal(db_path=tmp_path / "j.db") as tj:
        dec = live_and_journal(cfg, dry_run=True, journal=tj, data=_uptrend())
        rows = tj.live_log()

    assert dec.symbol == "SPY"
    assert dec.target_state == "long"          # uptrend → wants long
    assert dec.action == "buy"                 # flat paper broker → buy
    assert len(rows) == 1
    assert int(rows.iloc[0]["dry_run"]) == 1   # dry-run recorded as such
    assert rows.iloc[0]["order_id"] is None    # nothing submitted in dry-run


def test_live_and_journal_executes_on_paper(tmp_path):
    cfg = LiveConfig(symbol="SPY", strategy="momentum", params={"lookback": 50},
                     broker="paper", max_bar_age_days=100_000)  # off: synthetic 2023 data
    with TradeJournal(db_path=tmp_path / "j.db") as tj:
        dec = live_and_journal(cfg, dry_run=False, journal=tj, data=_uptrend())
    assert dec.action == "buy" and dec.order_id is not None
