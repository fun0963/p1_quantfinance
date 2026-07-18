"""CLI coverage: the pure arg-parsing helpers, plus end-to-end command runs via
typer's CliRunner. `info` is fully offline; `backtest` is exercised end-to-end
with the data loader stubbed to a synthetic frame (no network, no cache)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest
import typer
from typer.testing import CliRunner

import quant.cli as cli
from quant.cli import _coerce, _parse_grid, _parse_legs, _parse_params

# rich force-enables ANSI color when it detects CI (GITHUB_ACTIONS etc.), which
# breaks plain-substring assertions on typer error output: the message survives
# but gets interleaved with escape codes inside a rich panel. Neutralize every
# color trigger so output is identical on a dev box and on any CI runner.
# (None deletes the variable for the invoke; NO_COLOR=1 covers future detectors.)
_RUNNER_ENV = {
    "NO_COLOR": "1",
    "TERM": "dumb",
    "FORCE_COLOR": None,
    "GITHUB_ACTIONS": None,
    "CI": None,
}
# r.output must be STDOUT ONLY: the --json contract is "one JSON document on
# stdout" while loguru chatter legitimately goes to stderr. Old click mixes
# them unless told not to; click >= 8.2 dropped the kwarg and separates always.
try:
    runner = CliRunner(env=_RUNNER_ENV, mix_stderr=False)  # type: ignore[call-arg]
except TypeError:
    runner = CliRunner(env=_RUNNER_ENV)


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


def test_backtest_from_spec(monkeypatch):
    monkeypatch.setattr(cli, "_load", lambda *a, **k: _synthetic())
    r = runner.invoke(cli.app, ["backtest", "--spec", "spy_momentum",
                                "--engine", "vectorbt", "--no-log"])
    assert r.exit_code == 0, r.output
    assert "SPY" in r.output and "momentum" in r.output  # spec supplied both


def test_backtest_without_symbol_or_spec_errors():
    r = runner.invoke(cli.app, ["backtest", "--engine", "vectorbt", "--no-log"])
    assert r.exit_code != 0
    assert "SYMBOL or --spec" in r.output


def test_lifecycle_command_all_specs(monkeypatch):
    monkeypatch.setattr(cli, "_load", lambda *a, **k: _synthetic(n=400))
    r = runner.invoke(cli.app, ["lifecycle", "--all"])
    # Both committed specs evaluated, each with an explicit verdict.
    assert "spy_momentum" in r.output and "qqq_ma_cross" in r.output
    assert "HOLD" in r.output or "RETIRE-REVIEW" in r.output
    assert r.exit_code in (0, 1)                   # exit code mirrors breach status


def test_lifecycle_unknown_spec_errors():
    r = runner.invoke(cli.app, ["lifecycle", "does_not_exist"])
    assert r.exit_code == 1
    assert "available" in r.output


def test_cfg_from_spec_maps_identity_and_risk():
    cfg = cli._cfg_from_spec("spy_momentum", broker="paper", mode="target", fraction=0.9)
    assert cfg.symbol == "SPY" and cfg.strategy == "momentum"
    assert cfg.params == {"lookback": 100}
    assert cfg.start == "2020-01-01" and cfg.timeframe == "1d"
    assert cfg.stop_loss == 0.05 and cfg.take_profit == 0.15
    assert cfg.max_position_notional == 50_000
    assert cfg.broker == "paper" and cfg.fraction == 0.9   # operational knobs from CLI


def test_cfg_from_spec_explicit_symbol_wins():
    cfg = cli._cfg_from_spec("spy_momentum", symbol="VOO", broker="paper",
                             mode="target", fraction=0.95)
    assert cfg.symbol == "VOO"                              # override, strategy unchanged
    assert cfg.strategy == "momentum"


def test_live_from_spec_stays_dry_run(monkeypatch):
    import quant.execution.scheduler as sched
    captured = {}

    def fake_live_and_journal(cfg, *, dry_run=True, **kw):
        captured.update(cfg=cfg, dry_run=dry_run)
        return SimpleNamespace(ts="2024-01-02", price=100.0, position_before=0.0,
                               target_state="long", action="buy", qty=10.0,
                               reason="test", blocked=None, order_id=None,
                               symbol=cfg.symbol, dry_run=dry_run)

    monkeypatch.setattr(sched, "live_and_journal", fake_live_and_journal)
    r = runner.invoke(cli.app, ["live", "--spec", "spy_momentum", "--broker", "paper"])
    assert r.exit_code == 0, r.output
    assert captured["dry_run"] is True                      # no --execute -> dry-run
    assert captured["cfg"].symbol == "SPY"
    assert captured["cfg"].stop_loss == 0.05                # risk block flowed from spec
    assert "DRY-RUN" in r.output and "spec=spy_momentum" in r.output


def test_live_without_symbol_or_spec_errors():
    r = runner.invoke(cli.app, ["live", "--broker", "paper"])
    assert r.exit_code != 0
    assert "SYMBOL or --spec" in r.output


def test_schedule_multiple_specs_one_process(monkeypatch):
    import quant.execution.scheduler as sched
    captured = {}

    def fake_run_schedule(cfgs, *, at, days, tz, every=None, dry_run, run_now):
        captured.update(cfgs=cfgs, dry_run=dry_run, every=every)

    monkeypatch.setattr(sched, "run_schedule", fake_run_schedule)
    r = runner.invoke(cli.app, ["schedule", "--spec", "spy_momentum",
                                "--spec", "qqq_ma_cross", "--broker", "paper"])
    assert r.exit_code == 0, r.output
    assert [c.symbol for c in captured["cfgs"]] == ["SPY", "QQQ"]
    assert captured["dry_run"] is True                      # dry-run default preserved
    assert captured["every"] is None                        # daily cron mode by default
    assert "momentum on SPY" in r.output and "ma_cross on QQQ" in r.output

    r2 = runner.invoke(cli.app, ["schedule", "--spec", "spy_momentum",
                                 "--broker", "paper", "--every", "5min"])
    assert r2.exit_code == 0, r2.output
    assert captured["every"] == "5min"                      # intraday interval mode
    assert "every 5min (market hours only)" in r2.output


def test_schedule_execute_banner_is_cp950_safe(monkeypatch):
    """Regression: the --execute branch's banner carried U+26A0 and crashed the
    Windows cp950 console the FIRST time anyone ran a real scheduled execute
    (the dry-run branch was pure ASCII, so it never tripped offline)."""
    import quant.execution.scheduler as sched
    monkeypatch.setattr(sched, "run_schedule",
                        lambda cfgs, *, at, days, tz, every=None, dry_run, run_now: None)
    r = runner.invoke(cli.app, ["schedule", "--spec", "spy_momentum",
                                "--broker", "paper", "--execute"])
    assert r.exit_code == 0, r.output
    r.output.encode("cp950")                                # must not raise
    assert "live order routing is ON" in r.output


def test_schedule_rejects_symbol_and_spec_together(monkeypatch):
    r = runner.invoke(cli.app, ["schedule", "SPY", "--spec", "spy_momentum"])
    assert r.exit_code != 0
    assert "not both" in r.output


def test_note_new_and_list_end_to_end(tmp_path):
    r = runner.invoke(cli.app, ["note", "new", "buffer filter idea", "--strategy", "momentum",
                                "--symbols", "spy", "--experiments", "7", "--dir", str(tmp_path)])
    assert r.exit_code == 0, r.output
    assert "note created" in r.output

    r2 = runner.invoke(cli.app, ["note", "list", "--dir", str(tmp_path)])
    assert r2.exit_code == 0, r2.output
    assert "buffer filter idea" in r2.output
    assert "[momentum]" in r2.output and "exp=7" in r2.output


def test_note_list_empty_dir_hints_how_to_start(tmp_path):
    r = runner.invoke(cli.app, ["note", "list", "--dir", str(tmp_path / "none")])
    assert r.exit_code == 0
    assert "no notes yet" in r.output


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


# --- --json machine-readable output ------------------------------------------
# The contract (what AI agents / scripts rely on): exactly ONE parseable JSON
# document on stdout, top-level `command` + `data` (+ `ok` for pass/fail
# commands, whose exit codes are unchanged), and numbers stay numbers.

def _json_doc(r):
    # r.stdout, NOT r.output: loguru legitimately logs to stderr and new click's
    # `output` interleaves both streams. The contract is stdout purity.
    doc = json.loads(r.stdout)          # raises if any human chatter leaked
    assert "command" in doc and "data" in doc
    return doc


def _tmp_journal(monkeypatch, tmp_path):
    import quant.execution as ex
    real = ex.TradeJournal
    monkeypatch.setattr(ex, "TradeJournal", lambda *a, **k: real(tmp_path / "j.db"))


def test_json_info():
    r = runner.invoke(cli.app, ["info", "--json"])
    assert r.exit_code == 0, r.output
    doc = _json_doc(r)
    assert doc["command"] == "info"
    assert "ma_cross" in doc["data"]["strategies"]
    assert doc["data"]["alpaca_key"] in ("set", "MISSING")   # never the key itself


def test_json_backtest_metrics_stay_numbers(monkeypatch):
    monkeypatch.setattr(cli, "_load", lambda *a, **k: _synthetic())
    r = runner.invoke(cli.app, ["backtest", "SPY", "--strategy", "ma_cross",
                                "--engine", "vectorbt", "--slippage-bps", "20",
                                "--no-log", "--json"])
    assert r.exit_code == 0, r.output
    doc = _json_doc(r)
    m = doc["data"]["engines"]["vectorbt"]
    assert isinstance(m["sharpe"], (int, float))             # numpy scalar -> number
    assert isinstance(m["num_trades"], int)
    assert doc["data"]["cost"]["slippage_bps"] == 20.0


def test_json_live_dry_run(monkeypatch):
    import quant.execution.scheduler as sched

    def fake(cfg, *, dry_run=True, **kw):
        return SimpleNamespace(ts="2024-01-02", price=100.0, position_before=0.0,
                               target_state="long", action="buy", qty=10.0,
                               reason="test", blocked=None, order_id=None,
                               symbol=cfg.symbol, dry_run=dry_run)

    monkeypatch.setattr(sched, "live_and_journal", fake)
    r = runner.invoke(cli.app, ["live", "--spec", "spy_momentum",
                                "--broker", "paper", "--json"])
    assert r.exit_code == 0, r.output
    doc = _json_doc(r)
    assert doc["data"]["action"] == "buy" and doc["data"]["dry_run"] is True
    assert doc["data"]["spec"] == "spy_momentum"


def test_json_journal_modes_on_empty_db(monkeypatch, tmp_path):
    _tmp_journal(monkeypatch, tmp_path)
    r = runner.invoke(cli.app, ["journal", "--json"])
    assert r.exit_code == 0, r.output
    d = _json_doc(r)["data"]
    assert d["mode"] == "sessions" and d["records"] == []
    r2 = runner.invoke(cli.app, ["journal", "--live", "--json"])
    d2 = _json_doc(r2)["data"]
    assert d2["mode"] == "live" and d2["records"] == []


def test_json_oms_and_tca_on_empty_journal(monkeypatch, tmp_path):
    _tmp_journal(monkeypatch, tmp_path)
    r = runner.invoke(cli.app, ["oms", "--json"])
    assert r.exit_code == 0, r.output
    assert _json_doc(r)["data"]["records"] == []
    r2 = runner.invoke(cli.app, ["tca", "--json"])
    assert r2.exit_code == 0, r2.output
    d2 = _json_doc(r2)["data"]
    assert d2["n_filled"] == 0 and d2["per_order"] == []


def test_json_health_exit_mirrors_ok(monkeypatch, tmp_path):
    _tmp_journal(monkeypatch, tmp_path)
    r = runner.invoke(cli.app, ["health", "--json"])
    doc = _json_doc(r)
    assert isinstance(doc["ok"], bool) and isinstance(doc["data"]["components"], list)
    assert r.exit_code == (0 if doc["ok"] else 1)


def test_json_reconcile_paper_broker_clean(monkeypatch, tmp_path):
    _tmp_journal(monkeypatch, tmp_path)
    r = runner.invoke(cli.app, ["reconcile", "--broker", "paper", "--json"])
    assert r.exit_code == 0, r.output
    doc = _json_doc(r)
    assert doc["ok"] is True and doc["data"]["issues"] == []


def test_json_lifecycle_all(monkeypatch):
    monkeypatch.setattr(cli, "_load", lambda *a, **k: _synthetic(n=400))
    r = runner.invoke(cli.app, ["lifecycle", "--all", "--json"])
    doc = _json_doc(r)
    names = {s["name"] for s in doc["data"]["specs"]}
    assert {"spy_momentum", "qqq_ma_cross"} <= names
    assert all(s["verdict"] in ("hold", "retire-review") for s in doc["data"]["specs"])
    assert r.exit_code == (0 if doc["ok"] else 1)


def test_json_check(monkeypatch):
    monkeypatch.setattr(cli, "_load", lambda *a, **k: _synthetic())
    r = runner.invoke(cli.app, ["check", "SPY", "--json"])
    doc = _json_doc(r)
    assert doc["ok"] is True and doc["data"]["n_bars"] == 300
    assert r.exit_code == 0


def test_json_experiments_empty(monkeypatch, tmp_path):
    import quant.research as research
    real = research.ExperimentStore
    monkeypatch.setattr(research, "ExperimentStore", lambda *a, **k: real(db_path=tmp_path / "e.db"))
    r = runner.invoke(cli.app, ["experiments", "--json"])
    assert r.exit_code == 0, r.output
    d = _json_doc(r)["data"]
    assert d["mode"] == "list" and d["records"] == []


def test_json_note_list(tmp_path):
    runner.invoke(cli.app, ["note", "new", "an idea", "--dir", str(tmp_path)])
    r = runner.invoke(cli.app, ["note", "list", "--dir", str(tmp_path), "--json"])
    doc = _json_doc(r)
    assert doc["command"] == "note.list"
    assert doc["data"]["records"][0]["title"] == "an idea"
    assert doc["data"]["records"][0]["status"] == "idea"


def test_json_walkforward(monkeypatch):
    monkeypatch.setattr(cli, "_load", lambda *a, **k: _synthetic(n=300))
    r = runner.invoke(cli.app, ["walkforward", "SPY", "--strategy", "ma_cross",
                                "--train-bars", "120", "--test-bars", "60", "--json"])
    assert r.exit_code == 0, r.output
    doc = _json_doc(r)
    assert "wf_efficiency" in doc["data"]["summary"]
    assert doc["data"]["folds"] and doc["data"]["verdict"]


def test_json_status_offline(monkeypatch, tmp_path):
    _tmp_journal(monkeypatch, tmp_path)
    r = runner.invoke(cli.app, ["status", "--offline", "--json"])
    assert r.exit_code == 0, r.output
    doc = _json_doc(r)
    d = doc["data"]
    assert d["broker"] == {"skipped": "offline"}
    assert d["health"]["ok"] is True                     # empty journal: nothing stale
    assert d["tca"]["n_filled"] == 0
    assert {s["name"] for s in d["specs"]} >= {"spy_momentum"}
    assert doc["ok"] is True


def test_json_status_paper_broker_full(monkeypatch, tmp_path):
    _tmp_journal(monkeypatch, tmp_path)
    r = runner.invoke(cli.app, ["status", "--broker", "paper", "--json"])
    assert r.exit_code == 0, r.output
    d = _json_doc(r)["data"]
    assert d["broker"]["reconcile"]["ok"] is True
    assert d["broker"]["positions"] == []


def test_json_status_broker_failure_degrades(monkeypatch, tmp_path):
    _tmp_journal(monkeypatch, tmp_path)

    def boom(_name):
        raise RuntimeError("api down")

    monkeypatch.setattr(cli, "_live_broker", boom)
    r = runner.invoke(cli.app, ["status", "--json"])
    doc = _json_doc(r)
    assert "error" in doc["data"]["broker"]              # failed section reported...
    assert "components" in doc["data"]["health"]         # ...local sections survive
    assert doc["ok"] is False and r.exit_code == 1


def test_status_human_offline_smoke(monkeypatch, tmp_path):
    _tmp_journal(monkeypatch, tmp_path)
    r = runner.invoke(cli.app, ["status", "--offline"])
    assert r.exit_code == 0, r.output
    assert "overall   : ok" in r.output and "spy_momentum" in r.output
