"""The one-click backtest report writes a self-contained HTML tear sheet."""
from __future__ import annotations

import numpy as np
import pandas as pd

from quant.backtest.base import BacktestResult
from quant.backtest.metrics import compute_metrics
from quant.backtest.report import _trade_marks, build_report


def _result(seed=3, n=400):
    idx = pd.date_range("2020-01-01", periods=n, freq="B", tz="UTC")
    rng = np.random.default_rng(seed)
    eq = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0004, 0.01, n))), index=idx, name="equity")
    return BacktestResult(equity_curve=eq, metrics=compute_metrics(eq), engine="vectorbt")


def _bars(idx, seed=7):
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0.03, 1, len(idx))), index=idx).abs() + 10
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                         "close": close, "volume": 1e6}, index=idx)


def test_build_report_writes_self_contained_html(tmp_path):
    res = _result()
    out = build_report(res, symbol="SPY", strategy="ma_cross", metrics=res.metrics,
                       out_path=tmp_path / "r.html", subtitle="test run")
    assert out.exists()
    html = out.read_text(encoding="utf-8")
    # all panels + a metrics row are present (no `data` -> classic three panels)
    assert "Equity curve" in html and "Drawdown" in html and "Monthly returns" in html
    assert "Sharpe" in html and "Sortino" in html
    assert "Price & trades" not in html and "Benchmark" not in html
    # self-contained: the plotly.js library is embedded inline (multi-MB), not a CDN link
    assert "Plotly" in html and len(html) > 1_000_000


def test_report_with_data_adds_price_panel_and_benchmark(tmp_path):
    res = _result()
    data = _bars(res.equity_curve.index)
    trades = pd.DataFrame({                      # vectorbt records_readable schema
        "Size": [10.0, 12.0],
        "Entry Timestamp": [data.index[30], data.index[120]],
        "Avg Entry Price": [float(data["close"].iloc[30]), float(data["close"].iloc[120])],
        "Exit Timestamp": [data.index[90], data.index[200]],
        "Avg Exit Price": [float(data["close"].iloc[90]), float(data["close"].iloc[200])],
        "PnL": [1234.5, -321.0],
    })
    res.trades = trades
    out = build_report(res, symbol="SPY", strategy="momentum", metrics=res.metrics,
                       data=data, out_path=tmp_path / "r.html")
    html = out.read_text(encoding="utf-8")
    assert "Price & trades" in html and '"candlestick"' in html
    assert "Equity vs buy &amp; hold" in html or "Equity vs buy & hold" in html
    assert "Benchmark (buy &amp; hold)" in html or "Benchmark (buy & hold)" in html
    assert "Excess vs benchmark" in html
    # both marker traces made it in
    assert '"entry"' in html and '"exit"' in html
    # trade table has Size+prices -> annualized turnover row; PSR always tabulated
    assert "Turnover (annual)" in html
    assert "PSR (Sharpe&gt;0)" in html or "PSR (Sharpe>0)" in html


def test_trade_marks_backtrader_schema_falls_back_to_close(tmp_path):
    """backtrader's trade log has entry_time/entry_price but NO exit price -
    exit markers must land on the nearest bar close (naive timestamps too)."""
    idx = pd.date_range("2021-01-01", periods=50, freq="B", tz="UTC")
    data = _bars(idx)
    trades = pd.DataFrame({
        "entry_time": [idx[5].tz_localize(None)],   # backtrader emits naive dts
        "exit_time": [idx[20].tz_localize(None)],
        "entry_price": [123.45],
        "pnl": [10.0], "PnL": [9.5],
    })
    entries, exits = _trade_marks(trades, data)
    assert entries is not None and float(entries["px"].iloc[0]) == 123.45
    assert exits is not None
    assert float(exits["px"].iloc[0]) == float(data["close"].iloc[20])  # close fallback
    assert float(exits["pnl"].iloc[0]) == 9.5                           # net-of-commission col wins


def test_rolling_sharpe_panel_present_and_matches_lifecycle_convention(tmp_path):
    """The last rolling point must equal compute_metrics' Sharpe on the same
    trailing slice - the chart and the lifecycle rules speak the same number."""
    from quant.backtest.report import _rolling_sharpe

    res = _result(n=400)
    eq = res.equity_curve
    roll = _rolling_sharpe(eq, 252, 252.0)
    expected = compute_metrics(eq.iloc[-253:])["sharpe"]     # 252 return obs
    assert roll is not None
    assert abs(float(roll.iloc[-1]) - float(expected)) < 0.01

    out = build_report(res, symbol="SPY", strategy="momentum", metrics=res.metrics,
                       out_path=tmp_path / "r.html")
    assert "Rolling Sharpe (252-bar window, annualized)" in out.read_text(encoding="utf-8")


def test_rolling_sharpe_panel_omitted_on_short_series(tmp_path):
    res = _result(n=100)                                     # < window + margin
    out = build_report(res, symbol="SPY", strategy="momentum", metrics=res.metrics,
                       out_path=tmp_path / "r.html")
    html = out.read_text(encoding="utf-8")
    assert "Rolling Sharpe" not in html
    assert "Equity curve" in html                            # report still builds


def test_benchmark_rows_do_not_mutate_caller_metrics(tmp_path):
    res = _result()
    data = _bars(res.equity_curve.index)
    metrics_in = dict(res.metrics)
    build_report(res, symbol="SPY", strategy="momentum", metrics=metrics_in,
                 data=data, out_path=tmp_path / "r.html")
    assert "benchmark_return_pct" not in metrics_in     # caller's dict untouched
