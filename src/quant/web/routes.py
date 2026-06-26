"""Dashboard API routes — thin adapters over the existing research/journal code.

Every handler just loads data + calls a function the CLI already uses
(`load_bars`, `VectorBTEngine.run`, `run_portfolio`, `TradeJournal`), then shapes
the result into JSON the single-page frontend can render. Read-only: nothing here
places an order.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
from fastapi import APIRouter, HTTPException

from quant.web.schemas import BacktestRequest, PortfolioRequest

router = APIRouter()


def _equity_json(equity: pd.Series) -> dict:
    """Plot-ready {dates, values} from an equity-curve Series."""
    return {
        "dates": [str(ts.date()) for ts in equity.index],
        "values": [round(float(v), 2) for v in equity.to_numpy()],
    }


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
    from quant.backtest.vectorbt_engine import VectorBTEngine
    from quant.strategies.registry import get_strategy_cls

    try:
        strat = get_strategy_cls(req.strategy)(**req.params)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        data = _load(req.symbol, req.start, req.timeframe)
        res = VectorBTEngine(cash=req.cash).run(strat, data, timeframe=req.timeframe)
    except Exception as exc:  # data/network/engine failures -> 500 with the message
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

    return {
        "symbol": req.symbol,
        "strategy": req.strategy,
        "params": req.params,
        "bars": int(len(data)),
        "period": [str(data.index[0].date()), str(data.index[-1].date())],
        "metrics": res.metrics,
        "equity": _equity_json(res.equity_curve),
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
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}") from exc

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


@router.get("/journal/sessions", summary="Recent paper/live sessions")
def journal_sessions(limit: int = 20) -> dict:
    from quant.execution import TradeJournal

    with TradeJournal() as tj:
        df = tj.sessions(limit=limit)
    return {"rows": df.to_dict(orient="records")}


@router.get("/journal/live", summary="Recent live-runner decisions")
def journal_live(limit: int = 30) -> dict:
    from quant.execution import TradeJournal

    with TradeJournal() as tj:
        df = tj.live_log(limit=limit)
    return {"rows": df.where(pd.notna(df), None).to_dict(orient="records")}
