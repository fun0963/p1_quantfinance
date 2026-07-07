"""Experiment record system: record, query, and full-record retrieval.

Hermetic — the store is created under tmp_path and git metadata is injected, so
no real db is written and no subprocess runs."""
from __future__ import annotations

from quant.research import ExperimentStore

_METRICS = {"total_return_pct": 12.3, "sharpe": 1.1, "max_drawdown_pct": -8.0,
            "num_trades": 9, "final_equity": 112_300.0}


def _record(store, symbol="SPY", strategy="ma_cross", engine="vectorbt", metrics=None):
    return store.record(
        kind="backtest", symbol=symbol, strategy=strategy, params={"fast": 5, "slow": 20},
        start="2020-01-01", timeframe="1d", engine=engine, fees_bps=5.0, slippage_bps=2.0,
        data_bars=500, data_start="2020-01-02", data_end="2021-12-31",
        metrics=metrics or _METRICS, notes="unit", git=("abc1234", False),
    )


def test_record_promotes_headline_metrics_to_columns(tmp_path):
    with ExperimentStore(db_path=tmp_path / "e.db") as store:
        eid = _record(store)
        assert eid == 1
        row = store.recent().iloc[0]
        assert row["strategy"] == "ma_cross" and row["symbol"] == "SPY"
        assert row["sharpe"] == 1.1 and row["num_trades"] == 9
        assert row["git_hash"] == "abc1234"
        assert row["fees_bps"] == 5.0 and row["slippage_bps"] == 2.0


def test_get_decodes_params_and_metrics_json(tmp_path):
    with ExperimentStore(db_path=tmp_path / "e.db") as store:
        eid = _record(store)
        rec = store.get(eid)
        assert rec is not None
        assert rec["params"] == {"fast": 5, "slow": 20}      # decoded from json
        assert rec["metrics"]["final_equity"] == 112_300.0
        assert rec["git_dirty"] is False
        assert rec["notes"] == "unit"


def test_get_missing_returns_none(tmp_path):
    with ExperimentStore(db_path=tmp_path / "e.db") as store:
        assert store.get(999) is None


def test_recent_filters_by_strategy_and_symbol(tmp_path):
    with ExperimentStore(db_path=tmp_path / "e.db") as store:
        _record(store, strategy="ma_cross", symbol="SPY")
        _record(store, strategy="momentum", symbol="QQQ")
        _record(store, strategy="momentum", symbol="SPY")

        assert len(store.recent()) == 3
        assert set(store.recent(strategy="momentum")["strategy"]) == {"momentum"}
        spy_mom = store.recent(strategy="momentum", symbol="SPY")
        assert len(spy_mom) == 1


def test_dirty_flag_persists(tmp_path):
    with ExperimentStore(db_path=tmp_path / "e.db") as store:
        store.record(kind="backtest", symbol="SPY", strategy="ma_cross", params=None,
                     start="2020-01-01", timeframe="1d", engine="vectorbt", fees_bps=5.0,
                     slippage_bps=0.0, data_bars=10, data_start="2020-01-02",
                     data_end="2020-01-15", metrics=_METRICS, git=("deadbee", True))
        assert store.get(1)["git_dirty"] is True
