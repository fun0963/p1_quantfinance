"""Smoke tests — prove the skeleton imports and the core contracts hold.

These run without any API keys or network. Expand alongside each layer.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from quant.core.types import OrderSide, Signal, SignalType
from quant.risk.base import FixedFractionRisk
from quant.strategies.base import BaseStrategy


def test_package_imports():
    import quant

    assert quant.__version__


def test_strategy_contract_enforced():
    # BaseStrategy is abstract — must implement generate_signals.
    class Dummy(BaseStrategy):
        name = "dummy"

        def generate_signals(self, data: pd.DataFrame) -> pd.DataFrame:
            return pd.DataFrame(
                {"entries": False, "exits": False}, index=data.index
            )

    df = pd.DataFrame(
        {"close": [1, 2, 3]},
        index=pd.date_range("2024-01-01", periods=3, tz="UTC"),
    )
    out = Dummy().generate_signals(df)
    assert {"entries", "exits"} <= set(out.columns)


def test_fixed_fraction_sizing():
    risk = FixedFractionRisk(fraction=0.1)
    sig = Signal(
        symbol="SPY",
        timestamp=datetime.now(UTC),
        type=SignalType.ENTRY_LONG,
    )
    order = risk.size(sig, price=100.0, cash=10_000.0)
    assert order is not None
    assert order.side is OrderSide.BUY
    assert order.qty == 10.0  # 10% of 10k / $100
