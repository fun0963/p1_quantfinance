"""Dashboard API smoke tests. Skipped unless the [web] extra (fastapi) + httpx
(TestClient's transport) are installed — so the default [dev] CI run skips them,
mirroring the Timescale test's optional-dependency pattern. Offline: the data
loaders are monkeypatched to synthetic frames (no network)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # required by fastapi.testclient.TestClient

from fastapi.testclient import TestClient  # noqa: E402

from quant.web.app import create_app  # noqa: E402

client = TestClient(create_app())


def _uptrend(n=200, slope=0.5, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2022-01-01", periods=n, freq="B", tz="UTC")
    close = pd.Series(100 + np.arange(n) * slope + rng.normal(0, 0.3, n).cumsum(), index=idx).clip(lower=1)
    return pd.DataFrame({"open": close, "high": close * 1.001, "low": close * 0.999,
                         "close": close, "volume": 1e6}, index=idx)


def test_health_and_index():
    assert client.get("/health").json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200 and "Quant Results Dashboard" in page.text


def test_strategies_lists_registry():
    names = {s["name"] for s in client.get("/api/strategies").json()["strategies"]}
    assert {"ma_cross", "momentum"} <= names


def test_backtest_returns_metrics_and_equity(monkeypatch):
    import quant.web.routes as routes
    monkeypatch.setattr(routes, "_load", lambda *a, **k: _uptrend())
    r = client.post("/api/backtest", json={"symbol": "SPY", "strategy": "momentum",
                                           "params": {"lookback": 50}})
    assert r.status_code == 200
    body = r.json()
    assert "sharpe" in body["metrics"]
    assert len(body["equity"]["dates"]) == len(body["equity"]["values"]) > 0


def test_backtest_unknown_strategy_is_400():
    r = client.post("/api/backtest", json={"symbol": "SPY", "strategy": "does_not_exist"})
    assert r.status_code == 400


def test_portfolio_combines_legs(monkeypatch):
    import quant.portfolio.portfolio as pf
    monkeypatch.setattr(pf, "_load_symbol",
                        lambda symbol, start, timeframe: _uptrend(seed=sum(map(ord, symbol))))
    r = client.post("/api/portfolio", json={"legs": [
        {"symbol": "SPY", "strategy": "ma_cross", "weight": 0.5, "params": {"fast": 5, "slow": 20}},
        {"symbol": "QQQ", "strategy": "momentum", "weight": 0.5, "params": {"lookback": 20}},
    ]})
    assert r.status_code == 200
    body = r.json()
    assert len(body["legs"]) == 2
    assert len(body["correlation"]["labels"]) == 2
    assert "sharpe" in body["metrics"]


def test_backtest_includes_trade_stats_and_yearly(monkeypatch):
    import quant.web.routes as routes
    monkeypatch.setattr(routes, "_load", lambda *a, **k: _uptrend(n=400))
    body = client.post("/api/backtest", json={"symbol": "QQQ", "strategy": "momentum",
                                              "params": {"lookback": 50}}).json()
    assert "yearly_returns" in body
    # benchmark load is also stubbed to _uptrend, so alpha/beta computes
    assert "beta" in body["metrics"]


def test_sweep_returns_ranked_rows(monkeypatch):
    import quant.web.routes as routes
    monkeypatch.setattr(routes, "_load", lambda *a, **k: _uptrend(n=300))
    r = client.post("/api/sweep", json={"symbol": "SPY", "strategy": "ma_cross",
                                        "grid": {"fast": [5, 10], "slow": [20, 30]}, "top": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["total"] >= 1 and len(body["rows"]) >= 1
    assert "sharpe" in body["columns"]


def test_walkforward_returns_folds_and_summary(monkeypatch):
    import quant.web.routes as routes
    monkeypatch.setattr(routes, "_load", lambda *a, **k: _uptrend(n=900))
    r = client.post("/api/walkforward", json={"symbol": "SPY", "strategy": "momentum",
                                              "grid": {"lookback": [20, 50]},
                                              "train_bars": 300, "test_bars": 120})
    assert r.status_code == 200
    body = r.json()
    assert "wf_efficiency" in body["summary"]
    assert isinstance(body["folds"], list)


def test_backtest_slippage_lowers_equity_and_reports_cost(monkeypatch):
    import quant.web.routes as routes
    monkeypatch.setattr(routes, "_load", lambda *a, **k: _uptrend(n=400))

    def run(slip):
        return client.post("/api/backtest", json={"symbol": "SPY", "strategy": "ma_cross",
                           "params": {"fast": 5, "slow": 20}, "slippage_bps": slip}).json()

    base, slipped = run(0), run(50)
    assert "slippage 50.0 bps" in slipped["cost"]                       # cost surfaced + wired
    assert slipped["metrics"]["final_equity"] < base["metrics"]["final_equity"]


def test_500_error_redacts_dsn_password(monkeypatch):
    import quant.web.routes as routes

    def boom(*a, **k):
        raise RuntimeError("connect failed: postgresql://quant:secretpass@db:5432/quant")

    monkeypatch.setattr(routes, "_load", boom)
    r = client.post("/api/backtest", json={"symbol": "SPY", "strategy": "momentum",
                                           "params": {"lookback": 50}})
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert "secretpass" not in detail       # password redacted
    assert "***" in detail                   # ...but the shape is still shown
    assert "postgresql://quant:" in detail   # non-secret context preserved


def test_sweep_rejects_oversized_grid():
    # 71 * 71 = 5041 combos, over the 5000 cap -> 400 before any data load.
    grid = {"fast": list(range(1, 72)), "slow": list(range(1, 72))}
    r = client.post("/api/sweep", json={"symbol": "SPY", "strategy": "ma_cross", "grid": grid})
    assert r.status_code == 400
    assert "grid too large" in r.json()["detail"]


def test_walkforward_rejects_oversized_grid():
    grid = {"lookback": list(range(1, 5002))}  # 5001 combos > cap
    r = client.post("/api/walkforward", json={"symbol": "SPY", "strategy": "momentum", "grid": grid})
    assert r.status_code == 400
    assert "grid too large" in r.json()["detail"]


def test_journal_endpoint_shape():
    r = client.get("/api/journal/sessions")
    assert r.status_code == 200 and "rows" in r.json()
