"""Interactive HTML plots via plotly — pure-Python, zero native dependencies.

We deliberately avoid matplotlib here: its compiled `ft2font` extension fails to
load on some Windows setups (missing VC++ runtime), and static PNGs are worse for
research anyway. Plotly builds figures in pure Python and writes a self-contained
.html (plotly.js embedded) that opens in any browser — hover for daily values,
drag to zoom drawdowns. Output is offline-robust (no CDN needed).
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from quant.backtest.base import BacktestResult


def _drawdown(equity: pd.Series) -> pd.Series:
    """Drawdown series in percent (<= 0)."""
    eq = equity.astype(float)
    return (eq / eq.cummax() - 1.0) * 100.0


def plot_equity(
    results: BacktestResult | dict[str, BacktestResult],
    out_path: str | Path = "reports/equity.html",
    title: str = "Backtest — equity & drawdown",
) -> Path:
    """Equity curve (top) + drawdown (bottom) for one or more results, overlaid.

    Pass a single BacktestResult, or a {label: BacktestResult} dict to compare
    engines/strategies on the same axes.
    """
    from plotly.subplots import make_subplots

    if isinstance(results, BacktestResult):
        results = {results.engine or "result": results}

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.06,
        row_heights=[0.65, 0.35],
        subplot_titles=("Equity curve", "Drawdown (%)"),
    )
    for label, res in results.items():
        eq = res.equity_curve.dropna()
        fig.add_scatter(x=eq.index, y=eq.values, name=label, mode="lines", row=1, col=1)
        dd = _drawdown(eq)
        fig.add_scatter(x=dd.index, y=dd.values, name=f"{label} dd",
                        mode="lines", fill="tozeroy", row=2, col=1, showlegend=False)

    fig.update_layout(title=title, hovermode="x unified", template="plotly_white",
                      legend=dict(orientation="h", y=1.08))
    fig.update_yaxes(title_text="equity", row=1, col=1)
    fig.update_yaxes(title_text="drawdown %", row=2, col=1)

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out_path), include_plotlyjs=True)  # embed → offline-robust
    return out_path


def plot_heatmap(
    results: pd.DataFrame,
    metric: str = "sharpe",
    x_param: str | None = None,
    y_param: str | None = None,
    out_path: str | Path = "reports/heatmap.html",
    title: str | None = None,
) -> Path:
    """2-parameter sweep heatmap of `metric` as interactive HTML.

    x_param/y_param default to the first two non-metric columns of `results`.
    """
    import plotly.graph_objects as go

    metric_cols = {"total_return_pct", "sharpe", "max_drawdown_pct", "num_trades"}
    param_cols = [c for c in results.columns if c not in metric_cols]
    if len(param_cols) < 2:
        raise ValueError("heatmap needs at least 2 parameter columns")
    y_param = y_param or param_cols[0]
    x_param = x_param or param_cols[1]

    grid = results.pivot_table(index=y_param, columns=x_param, values=metric)
    fig = go.Figure(
        go.Heatmap(
            z=grid.values,
            x=[str(c) for c in grid.columns],
            y=[str(i) for i in grid.index],
            colorscale="Viridis",
            colorbar=dict(title=metric),
            text=np.round(grid.values, 2),
            texttemplate="%{text}",
            hovertemplate=f"{x_param}=%{{x}}<br>{y_param}=%{{y}}<br>{metric}=%{{z:.3f}}<extra></extra>",
        )
    )
    fig.update_layout(
        title=title or f"sweep — {metric}",
        xaxis_title=x_param, yaxis_title=y_param, template="plotly_white",
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out_path), include_plotlyjs=True)
    return out_path
