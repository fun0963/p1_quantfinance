"""One-click backtest report — a self-contained HTML tear sheet.

Assembles the things you actually look at after a run into one offline file:
a headline metrics table, the equity curve, the drawdown, and a monthly-returns
heatmap. Pure plotly + hand-built HTML (plotly.js embedded), no native deps and
no CDN — matches plots.py's rationale.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from quant.backtest.base import BacktestResult
from quant.backtest.metrics import monthly_returns

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# Metrics shown in the summary table, in order: (key, label, suffix).
_METRIC_ROWS = [
    ("final_equity", "Final equity", ""),
    ("total_return_pct", "Total return", "%"),
    ("cagr_pct", "CAGR", "%"),
    ("sharpe", "Sharpe", ""),
    ("sortino", "Sortino", ""),
    ("calmar", "Calmar", ""),
    ("max_drawdown_pct", "Max drawdown", "%"),
    ("num_trades", "Trades", ""),
    ("win_rate_pct", "Win rate", "%"),
    ("payoff_ratio", "Payoff ratio", ""),
    ("profit_factor", "Profit factor", ""),
]


def _drawdown(equity: pd.Series) -> pd.Series:
    eq = equity.astype(float)
    return (eq / eq.cummax() - 1.0) * 100.0


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
) -> Path:
    """Write a self-contained HTML tear sheet for one backtest result.

    `metrics` is the dict to tabulate (typically res.metrics merged with
    trade_stats). Returns the written path.
    """
    from plotly.subplots import make_subplots

    eq = result.equity_curve.dropna()
    dd = _drawdown(eq)
    mret = monthly_returns(eq)

    fig = make_subplots(
        rows=3, cols=1, vertical_spacing=0.09, row_heights=[0.4, 0.25, 0.35],
        subplot_titles=("Equity curve", "Drawdown (%)", "Monthly returns (%)"),
    )
    fig.add_scatter(x=eq.index, y=eq.values, mode="lines", name="equity", row=1, col=1)
    fig.add_scatter(x=dd.index, y=dd.values, mode="lines", fill="tozeroy",
                    name="drawdown", showlegend=False, row=2, col=1)
    if not mret.empty:
        z = mret.to_numpy(dtype=float)
        labels = np.where(np.isnan(z), "", np.round(z, 1).astype(str))  # blank empty months
        fig.add_heatmap(
            z=z, x=_MONTHS, y=[str(y) for y in mret.index],
            colorscale="RdYlGn", zmid=0, text=labels, texttemplate="%{text}",
            colorbar=dict(title="%"), row=3, col=1,
        )
    fig.update_layout(template="plotly_white", height=900, showlegend=False,
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
