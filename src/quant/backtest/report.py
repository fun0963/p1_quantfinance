"""One-click backtest report — a self-contained HTML tear sheet.

Assembles the things you actually look at after a run into one offline file:
a headline metrics table, price candlesticks WITH entry/exit markers (the most
direct sanity check of what the strategy actually did), the equity curve
overlaid with the symbol's own buy-and-hold benchmark, the drawdown, and a
monthly-returns heatmap. Pure plotly + hand-built HTML (plotly.js embedded),
no native deps and no CDN — matches plots.py's rationale.

The price/benchmark panels appear only when the caller passes `data` (OHLCV);
without it the report degrades to the original three panels.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from quant.backtest.base import BacktestResult
from quant.backtest.metrics import monthly_returns, turnover_annual

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Metrics shown in the summary table, in order: (key, label, suffix).
_METRIC_ROWS = [
    ("final_equity", "Final equity", ""),
    ("total_return_pct", "Total return", "%"),
    ("benchmark_return_pct", "Benchmark (buy & hold)", "%"),
    ("excess_return_pct", "Excess vs benchmark", "%"),
    ("cagr_pct", "CAGR", "%"),
    ("sharpe", "Sharpe", ""),
    ("psr_pct", "PSR (Sharpe>0)", "%"),
    ("sortino", "Sortino", ""),
    ("calmar", "Calmar", ""),
    ("max_drawdown_pct", "Max drawdown", "%"),
    ("num_trades", "Trades", ""),
    ("turnover_annual_x", "Turnover (annual)", "x"),
    ("win_rate_pct", "Win rate", "%"),
    ("payoff_ratio", "Payoff ratio", ""),
    ("profit_factor", "Profit factor", ""),
]

# Both engines' per-trade tables, one extractor: vectorbt's records_readable
# uses 'Entry Timestamp'/'Avg Entry Price'..., backtrader's log uses
# entry_time/entry_price (and has NO exit price - fall back to the bar close).
_TRADE_COLS = {
    "entry_ts": ("Entry Timestamp", "entry_time"),
    "exit_ts": ("Exit Timestamp", "exit_time"),
    "entry_px": ("Avg Entry Price", "entry_price"),
    "exit_px": ("Avg Exit Price", "exit_price"),
    "pnl": ("PnL", "pnl"),
}


def _pick(trades: pd.DataFrame, names: tuple) -> pd.Series | None:
    for n in names:
        if n in trades.columns:
            return trades[n]
    return None


def _as_index_tz(ts: pd.Series, like: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """Timestamps -> DatetimeIndex in the bar index's timezone (backtrader
    emits naive datetimes while the bar index is tz-aware UTC)."""
    idx = pd.DatetimeIndex(pd.to_datetime(ts))
    if idx.tz is None and like.tz is not None:
        idx = idx.tz_localize(like.tz)
    return idx


def _trade_marks(trades: pd.DataFrame, data: pd.DataFrame):
    """Normalize either engine's trade table into (entries, exits) marker
    frames with ts/px(/pnl). Returns (None, None) when nothing usable."""
    entry_ts = _pick(trades, _TRADE_COLS["entry_ts"])
    exit_ts = _pick(trades, _TRADE_COLS["exit_ts"])
    if entry_ts is None and exit_ts is None:
        return None, None
    close = data["close"].astype(float)

    def _frame(ts: pd.Series | None, px: pd.Series | None) -> pd.DataFrame | None:
        if ts is None or ts.empty:
            return None
        idx = _as_index_tz(ts, close.index)
        if px is not None:
            prices = px.astype(float).to_numpy()
        else:  # no price column: nearest bar's close
            pos = close.index.get_indexer(idx, method="nearest")
            prices = close.to_numpy()[pos]
        return pd.DataFrame({"ts": idx, "px": prices})

    entries = _frame(entry_ts, _pick(trades, _TRADE_COLS["entry_px"]))
    exits = _frame(exit_ts, _pick(trades, _TRADE_COLS["exit_px"]))
    pnl = _pick(trades, _TRADE_COLS["pnl"])
    if exits is not None and pnl is not None:
        exits["pnl"] = pnl.astype(float).to_numpy()
    return entries, exits


def _drawdown(equity: pd.Series) -> pd.Series:
    eq = equity.astype(float)
    return (eq / eq.cummax() - 1.0) * 100.0


def _rolling_sharpe(eq: pd.Series, window: int, ppy: float) -> pd.Series | None:
    """Annualized rolling Sharpe over `window` RETURN observations.

    Same convention as compute_metrics (mean/std, ddof=1, rf=0, *sqrt(ppy)), so
    the last point of this line equals the number the lifecycle rules check on
    the trailing window - the chart makes strategy decay visible ahead of a
    retire-review. None when the series is too short to say anything.
    """
    rets = eq.astype(float).pct_change(fill_method=None).dropna()
    if len(rets) < window + 5:
        return None
    rs = (rets.rolling(window).mean() / rets.rolling(window).std()) * np.sqrt(ppy)
    return rs.dropna()


def _metrics_table_html(metrics: dict) -> str:
    cells = []
    for key, label, suffix in _METRIC_ROWS:
        if key not in metrics or metrics[key] is None:
            continue
        val = metrics[key]
        shown = f"{val:,.2f}{suffix}" if isinstance(val, (int, float)) else str(val)
        cells.append(f"<tr><td>{label}</td><td class='v'>{shown}</td></tr>")
    return "<table class='metrics'>" + "".join(cells) + "</table>"


def build_report(
    result: BacktestResult,
    *,
    symbol: str,
    strategy: str,
    metrics: dict,
    out_path: str | Path = "reports/report.html",
    title: str | None = None,
    subtitle: str = "",
    data: pd.DataFrame | None = None,
    timeframe: str = "1d",
    rolling_window: int = 252,
) -> Path:
    """Write a self-contained HTML tear sheet for one backtest result.

    `metrics` is the dict to tabulate (typically res.metrics merged with
    trade_stats). Pass the OHLCV `data` the backtest ran on to also get the
    price+trade-marker panel and the buy-and-hold benchmark overlay/rows.
    `timeframe` annualizes the rolling Sharpe; `rolling_window` is in bars
    (default 252 = the lifecycle rules' default trailing window).
    Returns the written path.
    """
    from plotly.subplots import make_subplots

    from quant.data.timeframes import get_timeframe

    eq = result.equity_curve.dropna()
    dd = _drawdown(eq)
    mret = monthly_returns(eq)
    roll = _rolling_sharpe(eq, rolling_window, get_timeframe(timeframe).periods_per_year)

    bars = data if (data is not None and not data.empty) else None
    has_price = bars is not None
    metrics = dict(metrics)                       # never mutate the caller's dict
    tov = turnover_annual(result.trades, eq, timeframe)
    if tov is not None:
        metrics["turnover_annual_x"] = round(tov, 2)
    bench = None
    if bars is not None:
        close = bars["close"].astype(float)
        # Benchmark = holding the SAME symbol with the same starting equity:
        # the honest null hypothesis for a single-symbol timing strategy.
        bench = float(eq.iloc[0]) * close / float(close.iloc[0])
        bench_ret = (float(close.iloc[-1]) / float(close.iloc[0]) - 1.0) * 100.0
        metrics["benchmark_return_pct"] = round(bench_ret, 2)
        if isinstance(metrics.get("total_return_pct"), (int, float)):
            metrics["excess_return_pct"] = round(
                float(metrics["total_return_pct"]) - bench_ret, 2)

    panels: list[str] = (["price"] if has_price else []) + ["equity"] \
        + (["sharpe"] if roll is not None else []) + ["drawdown", "monthly"]
    _titles = {
        "price": "Price & trades",
        "equity": "Equity vs buy & hold" if has_price else "Equity curve",
        "sharpe": f"Rolling Sharpe ({rolling_window}-bar window, annualized)",
        "drawdown": "Drawdown (%)", "monthly": "Monthly returns (%)",
    }
    _hts = {"price": 0.30, "equity": 0.24, "sharpe": 0.14, "drawdown": 0.12, "monthly": 0.20}
    hsum = sum(_hts[p] for p in panels)
    row = {p: i + 1 for i, p in enumerate(panels)}
    fig = make_subplots(rows=len(panels), cols=1, vertical_spacing=0.06,
                        row_heights=[_hts[p] / hsum for p in panels],
                        subplot_titles=[_titles[p] for p in panels])

    if bars is not None:
        fig.add_candlestick(x=bars.index, open=bars["open"], high=bars["high"],
                            low=bars["low"], close=bars["close"],
                            name=symbol, showlegend=False, row=1, col=1)
        if result.trades is not None and len(result.trades):
            entries, exits = _trade_marks(result.trades, bars)
            if entries is not None:
                fig.add_scatter(
                    x=entries["ts"], y=entries["px"], mode="markers", name="entry",
                    marker=dict(symbol="triangle-up", size=11, color="#2ca02c",
                                line=dict(width=1, color="#14521a")),
                    hovertemplate="entry %{x|%Y-%m-%d %H:%M}<br>px %{y:.2f}<extra></extra>",
                    row=1, col=1)
            if exits is not None:
                custom = exits["pnl"] if "pnl" in exits else None
                fig.add_scatter(
                    x=exits["ts"], y=exits["px"], mode="markers", name="exit",
                    marker=dict(symbol="triangle-down", size=11, color="#d62728",
                                line=dict(width=1, color="#7a1516")),
                    customdata=custom,
                    hovertemplate=("exit %{x|%Y-%m-%d %H:%M}<br>px %{y:.2f}"
                                   + ("<br>PnL %{customdata:,.0f}" if custom is not None else "")
                                   + "<extra></extra>"),
                    row=1, col=1)
        # candlestick auto-adds a rangeslider on its axis - it wastes half the panel
        fig.update_layout(xaxis_rangeslider_visible=False)

    fig.add_scatter(x=eq.index, y=eq.values, mode="lines", name="strategy",
                    line=dict(color="#1f77b4", width=2), row=row["equity"], col=1)
    if bench is not None:
        fig.add_scatter(x=bench.index, y=bench.values, mode="lines", name="buy & hold",
                        line=dict(color="#888", width=1.5, dash="dot"), row=row["equity"], col=1)
    if roll is not None:
        fig.add_scatter(x=roll.index, y=roll.values, mode="lines", name="rolling sharpe",
                        line=dict(color="#9467bd", width=1.5), showlegend=False,
                        row=row["sharpe"], col=1)
        fig.add_hline(y=0.0, line=dict(color="#bbb", width=1, dash="dash"),
                      row=row["sharpe"], col=1)
    fig.add_scatter(x=dd.index, y=dd.values, mode="lines", fill="tozeroy",
                    name="drawdown", showlegend=False, row=row["drawdown"], col=1)
    if not mret.empty:
        z = mret.to_numpy(dtype=float)
        labels = np.where(np.isnan(z), "", np.round(z, 1).astype(str))  # blank empty months
        fig.add_heatmap(
            z=z, x=_MONTHS, y=[str(y) for y in mret.index],
            colorscale="RdYlGn", zmid=0, text=labels, texttemplate="%{text}",
            colorbar=dict(title="%"), row=row["monthly"], col=1,
        )
    fig.update_layout(template="plotly_white", height=280 * len(panels) + 60,
                      showlegend=has_price,
                      legend=dict(orientation="h", yanchor="bottom", y=1.015, x=0),
                      title=title or f"{strategy} on {symbol}")

    fig_html = fig.to_html(full_html=False, include_plotlyjs=True)
    page_title = title or f"{strategy} on {symbol}"
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>{page_title}</title><style>
 body{{font-family:system-ui,Arial,sans-serif;margin:24px;color:#222}}
 h1{{margin:0 0 2px}} .sub{{color:#666;margin:0 0 16px;font-size:14px}}
 table.metrics{{border-collapse:collapse;margin:0 0 20px}}
 table.metrics td{{border:1px solid #ddd;padding:4px 12px}}
 table.metrics td.v{{text-align:right;font-variant-numeric:tabular-nums}}
</style></head><body>
<h1>{page_title}</h1><p class="sub">{subtitle}</p>
{_metrics_table_html(metrics)}
{fig_html}
</body></html>"""

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html, encoding="utf-8")
    return out_path
