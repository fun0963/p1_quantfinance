"""Moving-average crossover — the canonical first strategy.

Long when the fast SMA crosses above the slow SMA; exit when it crosses back
below. Pure logic over a price frame: identical signals feed both backtest
engines, so any difference in results comes from execution modeling, not the
strategy. Long-only in phase 2.
"""
from __future__ import annotations

import pandas as pd

from quant.strategies.base import BaseStrategy


class MACrossStrategy(BaseStrategy):
    name = "ma_cross"

    def __init__(self, fast: int = 20, slow: int = 50) -> None:
        # Coerce: params often arrive as floats from a pandas sweep row.
        fast, slow = int(fast), int(slow)
        if fast >= slow:
            raise ValueError(f"fast ({fast}) must be < slow ({slow})")
        super().__init__(fast=fast, slow=slow)
        self.fast = fast
        self.slow = slow

    @classmethod
    def default_grid(cls) -> dict[str, list]:
        return {"fast": [5, 10, 15, 20, 30], "slow": [30, 50, 80, 120, 200]}

    @classmethod
    def params_valid(cls, **params) -> bool:
        return params["fast"] < params["slow"]

    def warmup_bars(self) -> int:
        return self.slow

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        close = data["close"]
        fast_ma = close.rolling(self.fast).mean()
        slow_ma = close.rolling(self.slow).mean()

        above = fast_ma > slow_ma
        prev_above = above.shift(fill_value=False)

        entries = above & ~prev_above          # crossed up this bar
        exits = ~above & prev_above            # crossed down this bar

        return pd.DataFrame(
            {
                "entries": entries.fillna(False),
                "exits": exits.fillna(False),
                "fast_ma": fast_ma,
                "slow_ma": slow_ma,
            },
            index=data.index,
        )
