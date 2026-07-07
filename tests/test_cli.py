"""CLI coverage: the pure arg-parsing helpers, plus end-to-end command runs via
typer's CliRunner. `info` is fully offline; `backtest` is exercised end-to-end
with the data loader stubbed to a synthetic frame (no network, no cache)."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import typer
from typer.testing import CliRunner

import quant.cli as cli
from quant.cli import _coerce, _parse_grid, _parse_legs, _parse_params

runner = CliRunner()


def _synthetic(n=300, seed=5):
    idx = pd.date_range("2021-01-01", periods=n, freq="B", tz="UTC")
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0.05, 1, n)), index=idx).abs() + 10
    return pd.DataFrame(
        {"open": close, "high": close * 1.01, "low": close * 0.99,
         "close": close, "volume": 1e6}, index=idx,
    )


# --- pure parsing helpers ---------------------------------------------------
def test_coerce_prefers_int_then_float_then_str():
    assert _coerce("5") == 5 and isinstance(_coerce("5"), int)
    assert _coerce("2.5") == 2.5 and isinstance(_coerce("2.5"), float)
    assert _coerce("ma") == "ma"


def test_parse_params():
    assert _parse_params("fast=20,slow=50") == {"fast": 20, "slow": 50}
    assert _parse_params("") == {}


def test_parse_grid():
    assert _parse_grid("fast=5,10,20;slow=50,100") == {"fast": [5, 10, 20], "slow": [50, 100]}
    assert _parse_grid(None) is None


def test_parse_legs_builds_legs_with_params():
    legs = _parse_legs("SPY:momentum:0.5:lookback=100;QQQ:ma_cross:0.5:fast=20,slow=50")
    assert len(legs) == 2
    assert legs[0].symbol == "SPY" and legs[0].weight == 0.5 and legs[0].params == {"lookback": 100}


def test_parse_legs_rejects_malformed_leg():
    with pytest.raises(typer.BadParameter):
        _parse_legs("SPY:momentum")  # missing weight


# --- end-to-end command runs ------------------------------------------------
def test_info_command_lists_settings_and_strategies():
    r = runner.invoke(cli.app, ["info"])
    assert r.exit_code == 0, r.output
    assert "strategies" in r.output
    assert "ma_cross" in r.output and "momentum" in r.output


def test_backtest_command_end_to_end(monkeypatch):
    monkeypatch.setattr(cli, "_load", lambda *a, **k: _synthetic())
    r = runner.invoke(cli.app, ["backtest", "SPY", "--strategy", "ma_cross",
                                "--params", "fast=5,slow=20", "--engine", "vectorbt", "--no-log"])
    assert r.exit_code == 0, r.output
    assert "vectorbt" in r.output and "sharpe" in r.output


def test_backtest_unknown_strategy_errors(monkeypatch):
    monkeypatch.setattr(cli, "_load", lambda *a, **k: _synthetic())
    r = runner.invoke(cli.app, ["backtest", "SPY", "--strategy", "does_not_exist",
                                "--engine", "vectorbt"])
    assert r.exit_code != 0


def test_backtest_reports_cost_model(monkeypatch):
    monkeypatch.setattr(cli, "_load", lambda *a, **k: _synthetic())
    r = runner.invoke(cli.app, ["backtest", "SPY", "--strategy", "ma_cross",
                                "--engine", "vectorbt", "--slippage-bps", "20", "--no-log"])
    assert r.exit_code == 0, r.output
    assert "cost model" in r.output and "slippage 20.0 bps" in r.output


def test_backtest_calibrate_from_tca(monkeypatch):
    monkeypatch.setattr(cli, "_load", lambda *a, **k: _synthetic())

    import quant.execution as ex
    import quant.ops.tca as tca_mod

    class _DummyJournal:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ex, "TradeJournal", _DummyJournal)
    monkeypatch.setattr(tca_mod, "tca_report", lambda tj, strategy=None: SimpleNamespace(
        total_notional_usd=1_000_000, total_commission_usd=100.0, avg_slippage_bps=8.0))

    r = runner.invoke(cli.app, ["backtest", "SPY", "--strategy", "ma_cross",
                                "--engine", "vectorbt", "--calibrate", "--no-log"])
    assert r.exit_code == 0, r.output
    assert "fees 1.0 bps" in r.output and "slippage 8.0 bps" in r.output


def test_backtest_report_flag_invokes_builder(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_load", lambda *a, **k: _synthetic())

    import quant.backtest.report as rep
    captured = {}

    def fake_build(res, *, symbol, strategy, metrics, out_path, title=None, subtitle=""):
        captured.update(symbol=symbol, metrics=metrics)
        p = tmp_path / "r.html"
        p.write_text("x", encoding="utf-8")
        return p

    monkeypatch.setattr(rep, "build_report", fake_build)
    r = runner.invoke(cli.app, ["backtest", "SPY", "--strategy", "ma_cross",
                                "--engine", "vectorbt", "--report", "--no-log"])
    assert r.exit_code == 0, r.output
    assert "Report ->" in r.output
    assert captured["symbol"] == "SPY" and "sharpe" in captured["metrics"]


def test_backtest_logs_experiment_and_experiments_command(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_load", lambda *a, **k: _synthetic())

    # Point the experiment store at a temp db so the run is hermetic.
    import quant.research as research
    real = research.ExperimentStore
    monkeypatch.setattr(research, "ExperimentStore", lambda *a, **k: real(db_path=tmp_path / "exp.db"))

    r = runner.invoke(cli.app, ["backtest", "SPY", "--strategy", "ma_cross",
                                "--engine", "vectorbt", "--note", "smoke"])
    assert r.exit_code == 0, r.output
    assert "logged experiment" in r.output

    # The `experiments` command reads it back.
    r2 = runner.invoke(cli.app, ["experiments"])
    assert r2.exit_code == 0, r2.output
    assert "ma_cross" in r2.output and "SPY" in r2.output
