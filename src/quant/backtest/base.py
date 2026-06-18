"""Backtest engine abstraction over the dual-engine setup.

VectorBT (fast, vectorized — research/sweeps) and Backtrader (event-driven —
live-like validation) both implement `BacktestEngine`. A strategy runs through
either without modification. Results are normalized into `BacktestResult` so
reporting code is engine-agnostic.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import pandas as pd

from quant.strategies.base import BaseStrategy


@dataclass
class BacktestResult:
    """Engine-agnostic backtest output."""
    equity_curve: pd.Series              # portfolio value over time
    metrics: dict = field(default_factory=dict)  # normalized: return/sharpe/dd (compute_metrics)
    stats: dict = field(default_factory=dict)    # engine-native stats (may differ per engine)
    trades: pd.DataFrame | None = None
    engine: str = ""


class BacktestEngine(ABC):
    """Run a strategy over historical data and return normalized results."""

    name: str = "base"

    def __init__(self, cash: float = 100_000, fees: float = 0.0005) -> None:
        self.cash = cash
        self.fees = fees

    @abstractmethod
    def run(self, strategy: BaseStrategy, data: pd.DataFrame) -> BacktestResult:
        """Execute `strategy` on `data` and return a BacktestResult."""
