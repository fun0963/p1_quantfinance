"""Strategy abstraction — the contract every trading idea implements.

Design goal: write a strategy ONCE and run it on either backtest engine and,
later, live. A strategy is pure logic: given a price history, produce signals.
It never talks to a broker or a data vendor directly — that keeps it testable
and engine-agnostic. Concrete strategies live in sibling modules (not yet
written — phase 1 is architecture only).
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd


class BaseStrategy(ABC):
    """Base class for all strategies.

    Subclasses set `name` and implement `generate_signals`. Parameters are passed
    via __init__ kwargs and stored in `self.params` for logging/optimization.
    """

    name: str = "base"

    def __init__(self, **params) -> None:
        self.params = params

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        """Map an OHLCV DataFrame to signals.

        Args:
            data: OHLCV frame (see DataFeed.COLUMNS), tz-aware DatetimeIndex.

        Returns:
            DataFrame indexed like `data` with at least an `entries` (bool) and
            `exits` (bool) column. This boolean form feeds VectorBT directly and
            is trivially adapted for Backtrader. Extra columns (e.g. size) are
            allowed and ignored by engines that don't use them.
        """

    # --- Optimization hooks (override per strategy) -----------------------
    # These let the generic sweep / walk-forward drive ANY strategy without
    # knowing its parameters: the strategy declares its own search space and
    # which combos are valid.

    @classmethod
    def default_grid(cls) -> dict[str, list]:
        """Default parameter search space: {param_name: [values...]}."""
        return {}

    @classmethod
    def params_valid(cls, **params) -> bool:
        """Whether a parameter combo is admissible (e.g. fast < slow)."""
        return True

    def warmup_bars(self) -> int:
        """Bars of history the indicators need before signals are valid.

        Walk-forward prefixes each out-of-sample window with this many bars so
        the strategy can trade from the window's first bar. Override per strategy.
        """
        return 0

    def __repr__(self) -> str:  # helps debugging / experiment tracking
        return f"{type(self).__name__}(name={self.name!r}, params={self.params})"
