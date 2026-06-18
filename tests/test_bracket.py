"""Tests for bracket (stop-loss / take-profit) exits, unit + end-to-end."""
from __future__ import annotations

import numpy as np
import pandas as pd
from pytest import approx

from quant.execution.session import run_paper_session
from quant.risk.bracket import Bracket, BracketConfig
from quant.strategies.ma_cross import MACrossStrategy


# --- Bracket unit logic -----------------------------------------------------
def test_bracket_levels_set_from_entry():
    b = Bracket(entry_price=100.0, qty=10, cfg=BracketConfig(stop_pct=0.05, take_pct=0.10))
    assert b.stop == approx(95.0)
    assert b.take == approx(110.0)


def test_stop_fires_when_low_breaches():
    b = Bracket(100.0, 10, BracketConfig(stop_pct=0.05))
    assert b.check(high=101, low=99) is None         # 99 > 95, safe
    why, price = b.check(high=100, low=94)            # 94 <= 95 → stop
    assert why == "stop-loss" and price == approx(95.0)


def test_take_fires_when_high_breaches():
    b = Bracket(100.0, 10, BracketConfig(take_pct=0.10))
    assert b.check(high=105, low=99) is None
    why, price = b.check(high=111, low=108)
    assert why == "take-profit" and price == approx(110.0)


def test_stop_wins_when_bar_spans_both():
    b = Bracket(100.0, 10, BracketConfig(stop_pct=0.05, take_pct=0.10))
    # Bar range 90..115 touches both; conservative tie-break → stop.
    assert b.check(high=115, low=90)[0] == "stop-loss"


def test_trailing_stop_ratchets_up_but_never_down():
    b = Bracket(100.0, 10, BracketConfig(stop_pct=0.10, trailing=True))
    assert b.stop == 90.0
    b.check(high=120, low=110)        # hwm→120, stop→108
    assert b.stop == 108.0
    b.check(high=115, low=109)        # high lower; stop must not loosen
    assert b.stop == 108.0


# --- end-to-end through the session -----------------------------------------
def _synthetic(n=300, seed=5):
    idx = pd.date_range("2021-01-01", periods=n, freq="D", tz="UTC")
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0.05, 1, n)), index=idx).abs() + 10
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1e6}, index=idx,
    )


def test_session_records_bracket_exits():
    data = _synthetic()
    cfg = BracketConfig(stop_pct=0.03, take_pct=0.05)
    res = run_paper_session(MACrossStrategy(fast=5, slow=20), data, "SPY", bracket_cfg=cfg)
    # With tight brackets, most exits should come from stop/take, not the signal.
    bracket_exits = res.exit_reasons["stop-loss"] + res.exit_reasons["take-profit"]
    assert bracket_exits > 0
    assert sum(res.exit_reasons.values()) > 0


def test_tight_stop_caps_drawdown_vs_no_bracket():
    data = _synthetic(seed=9)
    strat = MACrossStrategy(fast=5, slow=20)
    no_bracket = run_paper_session(strat, data, "SPY")
    tight = run_paper_session(strat, data, "SPY",
                              bracket_cfg=BracketConfig(stop_pct=0.02))
    # A 2% stop should not produce a deeper max drawdown than running unprotected.
    assert tight.metrics["max_drawdown_pct"] >= no_bracket.metrics["max_drawdown_pct"]
