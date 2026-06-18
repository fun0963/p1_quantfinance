"""Tests for the SQLite trade journal — record a session, read it back."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.execution.journal import TradeJournal
from quant.execution.session import run_paper_session
from quant.risk.gate import RiskGate, RiskLimits
from quant.strategies.ma_cross import MACrossStrategy


def _synthetic(n=300, seed=5):
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0.05, 1, n)), index=idx).abs() + 10
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1e6}, index=idx,
    )


def test_record_and_read_back(tmp_path):
    data = _synthetic()
    res = run_paper_session(MACrossStrategy(fast=5, slow=20), data, "SPY")

    with TradeJournal(db_path=tmp_path / "j.db") as tj:
        sid = tj.record_session(res, symbol="SPY", strategy="ma_cross",
                                params={"fast": 5, "slow": 20}, init_cash=100_000)
        sessions = tj.sessions()
        fills = tj.fills(sid)

    assert sid == 1
    assert len(sessions) == 1
    row = sessions.iloc[0]
    assert row["symbol"] == "SPY" and row["strategy"] == "ma_cross"
    assert row["num_fills"] == len(res.fills)
    # fills round-trip with timestamps and sides preserved.
    assert len(fills) == len(res.fills)
    assert set(fills["side"]).issubset({"buy", "sell"})
    assert fills["ts"].notna().all()


def test_blocked_orders_are_persisted(tmp_path):
    data = _synthetic()
    gate = RiskGate(RiskLimits(max_position_notional=1.0))  # blocks every buy
    res = run_paper_session(MACrossStrategy(fast=5, slow=20), data, "SPY", gate=gate)

    with TradeJournal(db_path=tmp_path / "j.db") as tj:
        sid = tj.record_session(res, symbol="SPY", strategy="ma_cross",
                                init_cash=100_000)
        blocks = tj.blocked(sid)

    assert len(blocks) == len(res.blocked) > 0
    assert blocks["reason"].str.contains("exceeds cap").any()


def test_multiple_sessions_increment_ids(tmp_path):
    data = _synthetic()
    res = run_paper_session(MACrossStrategy(fast=5, slow=20), data, "SPY")
    with TradeJournal(db_path=tmp_path / "j.db") as tj:
        a = tj.record_session(res, symbol="SPY", strategy="ma_cross", init_cash=100_000)
        b = tj.record_session(res, symbol="QQQ", strategy="ma_cross", init_cash=50_000)
        assert (a, b) == (1, 2)
        assert len(tj.sessions()) == 2
