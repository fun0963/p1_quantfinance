"""Strategy registry — name → class lookup so the CLI and research tools can
drive any strategy generically (e.g. `quant sweep SPY --strategy momentum`).

Register a new strategy in one place here and it immediately works across
backtest / sweep / walk-forward without touching those modules.
"""
from __future__ import annotations

from quant.strategies.base import BaseStrategy
from quant.strategies.ma_cross import MACrossStrategy
from quant.strategies.momentum import MomentumStrategy

REGISTRY: dict[str, type[BaseStrategy]] = {
    MACrossStrategy.name: MACrossStrategy,
    MomentumStrategy.name: MomentumStrategy,
}


def get_strategy_cls(name: str) -> type[BaseStrategy]:
    try:
        return REGISTRY[name]
    except KeyError:
        raise ValueError(
            f"unknown strategy {name!r}; available: {sorted(REGISTRY)}"
        ) from None


def available() -> list[str]:
    return sorted(REGISTRY)
