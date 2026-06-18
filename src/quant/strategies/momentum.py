"""Time-series momentum — the second strategy, proving the interface extends.

Classic absolute (time-series) momentum: go long when price is above its level
`lookback` bars ago (positive trailing return), exit when it drops below. A
`buffer` band reduces whipsaw around the zero line. Same `entries`/`exits`
contract as MA-cross, so it runs on every engine and through sweep / walk-forward
with no extra wiring — only `default_grid` differs.
"""
from __future__ import annotations

import pandas as pd

from quant.strategies.base import BaseStrategy


class MomentumStrategy(BaseStrategy):
    name = "momentum"

    def __init__(self, lookback: int = 100, buffer: float = 0.0) -> None:
        # Coerce: params often arrive as floats from a pandas sweep row.
        lookback, buffer = int(lookback), float(buffer)
        if lookback < 1:
            raise ValueError("lookback must be >= 1")
        if buffer < 0:
            raise ValueError("buffer must be >= 0")
        super().__init__(lookback=lookback, buffer=buffer)
        self.lookback = lookback
        self.buffer = buffer

    @classmethod
    def default_grid(cls) -> dict[str, list]:
        return {"lookback": [20, 50, 100, 150, 200], "buffer": [0.0, 0.02, 0.05]}

    def warmup_bars(self) -> int:
        return self.lookback

    def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
        close = data["close"]
        trailing_return = close / close.shift(self.lookback) - 1.0

        long_state = trailing_return > self.buffer
        flat_state = trailing_return < -self.buffer
        prev_long = long_state.shift(fill_value=False)

        entries = long_state & ~prev_long       # crossed up through the band
        exits = flat_state & prev_long          # crossed down through the band

        return pd.DataFrame(
            {
                "entries": entries.fillna(False),
                "exits": exits.fillna(False),
                "trailing_return": trailing_return,
            },
            index=data.index,
        )
