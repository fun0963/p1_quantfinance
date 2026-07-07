"""The one-click backtest report writes a self-contained HTML tear sheet."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.backtest.base import BacktestResult
from quant.backtest.metrics import compute_metrics
from quant.backtest.report import build_report


def _result(seed=3, n=400):
    idx = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC")
    rng = np.random.default_rng(seed)
    eq = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, n))), index=idx, name="equity")
    return BacktestResult(equity_curve=eq, metrics=compute_metrics(eq), engine="vectorbt")


def test_build_report_writes_self_contained_html(tmp_path):
    res = _result()
    out = build_report(res, symbol="SPY", strategy="ma_cross", metrics=res.metrics,
                       out_path=tmp_path / "r.html", subtitle="test run")
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    # all four panels + a metrics row are present
    assert "Equity curve" in html and "Drawdown" in html and "Monthly returns" in html
    assert "Sharpe" in html and "Sortino" in html
    # self-contained: the plotly.js library is embedded inline (multi-MB), not a CDN link
    assert "Plotly" in html and len(html) > 1_000_000
