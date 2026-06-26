"""Pydantic request/response contracts for the dashboard API.

Requests are validated + surfaced as forms in `/docs`. Responses are kept as
plain dicts in routes for flexibility, but the equity-curve shape is documented
here so the frontend knows what to plot.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class BacktestRequest(BaseModel):
    symbol: str = "SPY"
    strategy: str = "momentum"
    params: dict[str, float] = Field(default_factory=dict, description="e.g. {\"lookback\": 100}")
    start: str = "2020-01-01"
    timeframe: str = "1d"
    cash: float = 100_000


class PortfolioLegIn(BaseModel):
    symbol: str
    strategy: str
    weight: float = 1.0
    params: dict[str, float] = Field(default_factory=dict)


class PortfolioRequest(BaseModel):
    legs: list[PortfolioLegIn]
    cash: float = 100_000
    start: str = "2020-01-01"
    timeframe: str = "1d"


class SweepRequest(BaseModel):
    symbol: str = "SPY"
    strategy: str = "momentum"
    grid: dict[str, list[float]] = Field(default_factory=dict, description="empty = strategy default grid")
    start: str = "2020-01-01"
    timeframe: str = "1d"
    sort_by: str = "sharpe"
    top: int = 20


class WalkforwardRequest(BaseModel):
    symbol: str = "SPY"
    strategy: str = "momentum"
    grid: dict[str, list[float]] = Field(default_factory=dict, description="empty = strategy default grid")
    start: str = "2015-01-01"
    timeframe: str = "1d"
    train_bars: int = 504
    test_bars: int = 126
    sort_by: str = "sharpe"


class EquityCurve(BaseModel):
    """Plot-ready equity curve: parallel date/value arrays."""
    dates: list[str]
    values: list[float]
