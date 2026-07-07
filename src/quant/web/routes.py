"""Dashboard API routes — thin adapters over the existing research/journal code.

Every handler just loads data + calls a function the CLI already uses
(`load_bars`, `VectorBTEngine.run`, `run_portfolio`, `TradeJournal`), then shapes
the result into JSON the single-page frontend can render. Read-only: nothing here
places an order.
"""
from __future__ import annotations

import json
import re
from datetime import UTC, datetime

import pandas as pd
from fastapi import APIRouter, HTTPException

from quant.utils import get_logger
from quant.web.schemas import (
    BacktestRequest,
    PortfolioRequest,
    SweepRequest,
    WalkforwardRequest,
)

router = APIRouter()
log = get_logger(__name__)

# Cap on user-supplied sweep/walk-forward grids: expand_grid materializes the
# full cartesian product before filtering, so an unbounded grid can OOM the box.
MAX_GRID_COMBOS = 5_000

# URL credentials (e.g. a Timescale DSN "postgresql://user:pass@host") that must
# never reach the client in an error message.
_CREDS_RE = re.compile(r"(://[^:/@\s]+:)[^@/\s]+(@)")


def _safe_detail(exc: Exception) -> str:
    """Client-safe error text: log the full error server-side, but redact any
    URL credentials so a DB connection failure can't leak the DSN password."""
    full = f"{type(exc).__name__}: {exc}"
    log.warning(f"request failed: {full}")
    return _CREDS_RE.sub(r"\1***\2", full)


def _check_grid_size(grid: dict[str, list]) -> None:
    """Reject an oversized user grid before expand_grid materializes it."""
    if not grid:
        return  # empty -> strategy default grid, which is bounded
    combos = 1
    for values in grid.values():
        combos *= max(len(values), 1)
    if combos > MAX_GRID_COMBOS:
        raise HTTPException(
            status_code=400,
            detail=f"grid too large: {combos} combos exceeds the {MAX_GRID_COMBOS} cap",
        )


def _equity_json(equity: pd.Series) -> dict:
    """Plot-ready {dates, values} from an equity-curve Series."""
    return {
        "dates": [str(ts.date()) for ts in equity.index],
        "values": [round(float(v), 2) for v in equity.to_numpy()],
    }


def _records(df: pd.DataFrame) -> list[dict]:
    """DataFrame -> list[dict] via pandas' JSON encoder, so numpy ints/floats and
    NaN are serialized cleanly (NaN -> null) for FastAPI."""
    return json.loads(df.to_json(orient="records", date_format="iso"))


def _load(symbol: str, start: str, timeframe: str) -> pd.DataFrame:
    from quant.data.feeds.yfinance_feed import YFinanceFeed
    from quant.data.loaders import load_bars

    start_dt = datetime.fromisoformat(start).replace(tzinfo=UTC)
    return load_bars(symbol, YFinanceFeed(), start=start_dt, timeframe=timeframe)


@router.get("/strategies", summary="List registered strategies + default grids")
def strategies() -> dict:
    from quant.strategies.registry import REGISTRY, available

    return {"strategies": [{"name": n, "default_grid": REGISTRY[n].default_grid()}
                           for n in available()]}


@router.post("/backtest", summary="Run a backtest (VectorBT) and return metrics + equity curve")
def backtest(req: BacktestRequest) -> dict:
    from quant.backtest.costs import CostModel
    from quant.backtest.vectorbt_engine import VectorBTEngine
    from quant.strategies.registry import get_strategy_cls

    try:
        strat = get_strategy_cls(req.strategy)(**req.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cost = CostModel(fees=req.fees_bps / 1e4, slippage=req.slippage_bps / 1e4)
    try:
        data = _load(req.symbol, req.start, req.timeframe)
        res = VectorBTEngine(cash=req.cash, fees=cost.fees, slippage=cost.slippage).run(
            strat, data, timeframe=req.timeframe)
    except Exception as exc:  # data/network/engine failures -> 500 (message redacted)
        raise HTTPException(status_code=500, detail=_safe_detail(exc)) from exc

    from quant.backtest.metrics import alpha_beta, trade_stats, yearly_returns

    metrics = {**res.metrics, **trade_stats(res.trades)}
    # Alpha / Beta vs SPY buy-and-hold (best-effort — never fail the backtest on it).
    try:
        bench_close = _load("SPY", req.start, req.timeframe)["close"]
        strat_ret = res.equity_curve.pct_change(fill_method=None)
        bench_ret = bench_close.reindex(res.equity_curve.index).ffill().pct_change(fill_method=None)
        metrics.update(alpha_beta(strat_ret, bench_ret))
    except Exception:  # noqa: BLE001 - benchmark is a bonus
        pass

    return {
        "symbol": req.symbol,
        "strategy": req.strategy,
        "params": req.params,
        "bars": int(len(data)),
        "period": [str(data.index[0].date()), str(data.index[-1].date())],
        "cost": cost.summary(),
        "metrics": metrics,
        "equity": _equity_json(res.equity_curve),
        "yearly_returns": yearly_returns(res.equity_curve),
    }


@router.post("/portfolio", summary="Allocate across legs and return blended + per-leg results")
def portfolio(req: PortfolioRequest) -> dict:
    from quant.portfolio import PortfolioLeg, run_portfolio

    legs = [PortfolioLeg(leg.symbol, leg.strategy, leg.params, leg.weight) for leg in req.legs]
    try:
        res = run_portfolio(legs, cash=req.cash, start=req.start, timeframe=req.timeframe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_safe_detail(exc)) from exc

    return {
        "init_cash": res.init_cash,
        "metrics": res.metrics,
        "weighted_avg_sharpe": res.weighted_avg_sharpe,
        "diversification_ratio": res.diversification_ratio,
        "legs": [{"label": f"{leg.symbol}:{leg.strategy}", "weight": round(leg.weight, 4),
                  "metrics": res.leg_metrics[f"{leg.symbol}:{leg.strategy}"]} for leg in res.legs],
        "correlation": {"labels": list(res.correlation.columns),
                        "matrix": res.correlation.to_numpy().tolist()},
        "equity": _equity_json(res.equity_curve),
    }


@router.post("/sweep", summary="Vectorized parameter sweep — ranked combos")
def sweep_endpoint(req: SweepRequest) -> dict:
    from quant.backtest.optimize import sweep
    from quant.strategies.registry import get_strategy_cls

    try:
        cls = get_strategy_cls(req.strategy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _check_grid_size(req.grid)
    try:
        data = _load(req.symbol, req.start, req.timeframe)
        results = sweep(cls, data, grid=req.grid or None, sort_by=req.sort_by, timeframe=req.timeframe)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_safe_detail(exc)) from exc

    return {
        "sort_by": req.sort_by,
        "total": int(len(results)),
        "columns": list(results.columns),
        "rows": _records(results.head(req.top).round(3)),
    }


@router.post("/walkforward", summary="Walk-forward out-of-sample validation")
def walkforward_endpoint(req: WalkforwardRequest) -> dict:
    from quant.backtest.walkforward import summarize, walk_forward
    from quant.strategies.registry import get_strategy_cls

    try:
        cls = get_strategy_cls(req.strategy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    _check_grid_size(req.grid)
    try:
        data = _load(req.symbol, req.start, req.timeframe)
        wf = walk_forward(cls, data, grid=req.grid or None, train_bars=req.train_bars,
                          test_bars=req.test_bars, sort_by=req.sort_by, timeframe=req.timeframe)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=_safe_detail(exc)) from exc

    return {"folds": _records(wf.round(3)), "summary": summarize(wf)}


@router.get("/journal/sessions", summary="Recent paper/live sessions")
def journal_sessions(limit: int = 20) -> dict:
    from quant.execution import TradeJournal

    with TradeJournal() as tj:
        df = tj.sessions(limit=limit)
    return {"rows": _records(df)}


@router.get("/journal/live", summary="Recent live-runner decisions")
def journal_live(limit: int = 30) -> dict:
    from quant.execution import TradeJournal

    with TradeJournal() as tj:
        df = tj.live_log(limit=limit)
    return {"rows": _records(df)}
