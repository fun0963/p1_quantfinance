"""Tests for backtest-vs-live decision drift."""
from __future__ import annotations

import pandas as pd

from quant.ops.drift import decision_drift, expected_action_bars

# live_log-shaped columns; order_id present => the order was actually PLACED
# (a blocked/dry-run decision has action buy/sell but order_id None).
_COLS = ["symbol", "strategy", "action", "bar_ts", "order_id"]


class _FakeStrat:
    """A strategy stub returning fixed entries/exits, to control expected trade bars."""
    def __init__(self, entries: pd.Series, exits: pd.Series):
        self._sig = pd.DataFrame({"entries": entries, "exits": exits})

    def generate_signals(self, data):
        return self._sig


def _fixture():
    idx = pd.date_range("2024-01-01", periods=5, freq="D")
    entries = pd.Series([True, False, False, False, False], index=idx)
    exits = pd.Series([False, False, False, True, False], index=idx)
    data = pd.DataFrame({"close": range(5)}, index=idx)
    return data, _FakeStrat(entries, exits), idx


def _live(rows):
    return pd.DataFrame(rows, columns=_COLS)


def _row(action, d, order_id="o"):
    return ("SPY", "x", action, str(d), order_id)


def test_expected_action_bars_finds_entry_and_exit():
    data, strat, idx = _fixture()
    exp = expected_action_bars(strat, data)
    assert exp == {idx[0].date(): "buy", idx[3].date(): "sell"}


def test_full_agreement_is_ok():
    data, strat, idx = _fixture()
    live = _live([_row("buy", idx[0].date()), _row("sell", idx[3].date())])
    rep = decision_drift(data, strat, live, symbol="SPY", strategy_name="x")
    assert rep.ok and rep.agreement == 1.0
    assert not rep.missed and not rep.extra


def test_missed_trade_flags_drift():
    data, strat, idx = _fixture()
    # Bought @idx0, still running through idx4 (hold row) but never sold @idx3.
    live = _live([_row("buy", idx[0].date()), _row("hold", idx[4].date(), None)])
    rep = decision_drift(data, strat, live, symbol="SPY", strategy_name="x")
    assert not rep.ok
    assert rep.missed == [(idx[3].date(), "sell")]
    assert rep.agreement == 0.5


def test_extra_live_action_flags_drift():
    data, strat, idx = _fixture()
    live = _live([_row("buy", idx[0].date()), _row("sell", idx[3].date()),
                  _row("buy", idx[1].date())])              # unexpected extra buy
    rep = decision_drift(data, strat, live, symbol="SPY", strategy_name="x")
    assert not rep.ok                                        # agreement 2/3 < 0.8
    assert (idx[1].date(), "buy") in rep.extra


def test_blocked_or_dryrun_decision_is_not_a_placed_trade():
    """A backtest entry the live runner was BLOCKED from taking (order_id None) must
    surface as a MISSED trade, not a false match — the core reason drift exists."""
    data, strat, idx = _fixture()
    live = _live([_row("buy", idx[0].date(), None),         # decided buy but blocked/dry-run
                  _row("sell", idx[3].date())])             # really sold
    rep = decision_drift(data, strat, live, symbol="SPY", strategy_name="x")
    assert (idx[0].date(), "buy") in rep.missed             # never actually entered
    assert not rep.ok


def test_pre_deployment_signals_not_falsely_missed():
    """Backtest signals from before the live runner was deployed must not count as
    missed — only the window the runner actually ran is compared."""
    data, strat, idx = _fixture()                           # expects buy@idx0, sell@idx3
    live = _live([_row("hold", idx[4].date(), None)])       # runner only ran at idx4
    rep = decision_drift(data, strat, live, symbol="SPY", strategy_name="x")
    assert rep.ok and rep.n_expected == 0                   # both expected trades predate coverage


def test_no_signals_no_actions_is_trivially_ok():
    idx = pd.date_range("2024-01-01", periods=3, freq="D")
    flat = pd.Series([False, False, False], index=idx)
    strat = _FakeStrat(flat, flat)
    data = pd.DataFrame({"close": range(3)}, index=idx)
    rep = decision_drift(data, strat, _live([]), symbol="SPY", strategy_name="x")
    assert rep.ok and rep.n_expected == 0 and rep.n_live == 0


def test_symbol_and_strategy_filter():
    data, strat, idx = _fixture()
    live = _live([_row("buy", idx[0].date()), _row("sell", idx[3].date()),
                  ("QQQ", "y", "buy", str(idx[1].date()), "o9")])   # other symbol/strategy
    rep = decision_drift(data, strat, live, symbol="SPY", strategy_name="x")
    assert rep.ok and rep.n_live == 2                       # QQQ/y filtered out
