"""Lifecycle discipline: pre-committed rules evaluated on a trailing window,
plus the named strategy-spec loader they live in. Offline and hermetic."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from quant.research.lifecycle import LifecycleRules, check_lifecycle
from quant.strategies.spec import get_spec, load_specs

ROOT = Path(__file__).resolve().parent.parent


def _equity(n=400, drift=0.001, vol=0.005, seed=1):
    idx = pd.date_range("2022-01-01", periods=n, freq="B", tz="UTC")
    rng = np.random.default_rng(seed)
    return pd.Series(100_000 * np.exp(np.cumsum(rng.normal(drift, vol, n))), index=idx)


# --- rules -------------------------------------------------------------------
def test_rules_from_dict_ignores_non_rule_keys():
    rules = LifecycleRules.from_dict({"state": "paper", "eval_bars": 100,
                                      "min_rolling_sharpe": 0.5})
    assert rules.eval_bars == 100
    assert rules.min_rolling_sharpe == 0.5
    assert rules.max_drawdown_pct == -25.0        # default preserved


# --- evaluation --------------------------------------------------------------
def test_healthy_strategy_holds():
    rep = check_lifecycle("s", state="paper", equity=_equity(drift=0.002),
                          num_trades=5, rules=LifecycleRules())
    assert rep.ok and rep.verdict == "hold"
    assert rep.window_bars == 252                  # trailing window, not full history


def test_sharpe_floor_breach_recommends_retire():
    losing = _equity(drift=-0.002)                 # persistent bleed
    rep = check_lifecycle("s", state="live", equity=losing,
                          num_trades=5, rules=LifecycleRules(min_rolling_sharpe=0.0))
    assert not rep.ok and rep.verdict == "retire-review"
    assert any("sharpe" in b for b in rep.breaches)


def test_drawdown_floor_breach_flagged():
    eq = _equity(drift=0.001)
    eq.iloc[-30:] = eq.iloc[-31] * 0.60            # sudden -40% crash at the end
    rep = check_lifecycle("s", state="live", equity=eq, num_trades=5,
                          rules=LifecycleRules(max_drawdown_pct=-25.0))
    assert any("drawdown" in b for b in rep.breaches)


def test_dead_strategy_is_unhealthy():
    rep = check_lifecycle("s", state="paper", equity=_equity(),
                          num_trades=0, rules=LifecycleRules(min_trades=1))
    assert any("trade" in b for b in rep.breaches)


def test_insufficient_history_is_a_breach_not_a_pass():
    rep = check_lifecycle("s", state="research", equity=_equity(n=1),
                          num_trades=0, rules=LifecycleRules())
    assert not rep.ok
    assert any("insufficient" in b for b in rep.breaches)


def test_summary_is_ascii_safe():
    rep = check_lifecycle("s", state="paper", equity=_equity(), num_trades=3,
                          rules=LifecycleRules())
    rep.summary().encode("cp950")                  # Windows console must not choke


# --- spec loading ------------------------------------------------------------
def test_load_committed_example_specs():
    specs = load_specs(ROOT / "configs" / "strategies.json")
    assert {"spy_momentum", "qqq_ma_cross"} <= set(specs)
    sp = specs["spy_momentum"]
    assert sp.symbol == "SPY" and sp.strategy == "momentum"
    assert sp.params == {"lookback": 100}
    assert sp.state == "paper"
    assert sp.risk["stop_loss"] == 0.05


def test_get_spec_unknown_name_lists_available(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"a": {"symbol": "SPY", "strategy": "momentum"}}), encoding="utf-8")
    with pytest.raises(KeyError, match="available"):
        get_spec("nope", path=p)


def test_spec_rejects_unknown_keys(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"a": {"symbol": "SPY", "strategy": "momentum",
                                   "sotp_loss": 0.05}}), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown key"):
        load_specs(p)


def test_spec_requires_symbol_and_strategy(tmp_path):
    p = tmp_path / "s.json"
    p.write_text(json.dumps({"a": {"symbol": "SPY"}}), encoding="utf-8")
    with pytest.raises(ValueError, match="needs at least"):
        load_specs(p)


def test_missing_spec_file_raises_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="strategy spec file not found"):
        load_specs(tmp_path / "absent.json")
