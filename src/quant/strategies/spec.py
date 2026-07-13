"""Named strategy specs — parameters externalized as versioned config (M6.3).

A spec bundles everything that defines a deployable strategy instance — symbol,
strategy name, parameters, data window, risk settings and pre-committed
lifecycle rules — in a JSON file under version control, so "what exactly are we
running?" is a reviewed diff, not a shell-history archaeology dig.

Config is data, not code (same rationale as portfolios/example.json): new
parameterizations don't touch Python. Default file: configs/strategies.json.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from config.settings import ROOT_DIR

DEFAULT_SPECS_PATH = ROOT_DIR / "configs" / "strategies.json"


@dataclass(frozen=True)
class StrategySpec:
    """One named, deployable strategy configuration."""
    name: str
    symbol: str
    strategy: str                                  # registry name (see quant info)
    params: dict = field(default_factory=dict)
    timeframe: str = "1d"
    start: str = "2020-01-01"
    risk: dict = field(default_factory=dict)       # stop_loss / take_profit / max_position_notional / max_daily_loss
    lifecycle: dict = field(default_factory=dict)  # state + rule overrides (see research/lifecycle.py)

    @property
    def state(self) -> str:
        """Lifecycle state recorded in the spec (research | paper | live | retired)."""
        return str(self.lifecycle.get("state", "research"))


def load_specs(path: str | Path | None = None) -> dict[str, StrategySpec]:
    """Parse the spec file into {name: StrategySpec}. Unknown keys are rejected so
    a typo'd field fails loudly instead of being silently ignored."""
    p = Path(path) if path else DEFAULT_SPECS_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"strategy spec file not found: {p} (create it or pass --config)")
    raw = json.loads(p.read_text(encoding="utf-8"))

    allowed = {"symbol", "strategy", "params", "timeframe", "start", "risk", "lifecycle"}
    specs: dict[str, StrategySpec] = {}
    for name, body in raw.items():
        unknown = set(body) - allowed
        if unknown:
            raise ValueError(f"spec {name!r} has unknown key(s): {sorted(unknown)}")
        if "symbol" not in body or "strategy" not in body:
            raise ValueError(f"spec {name!r} needs at least 'symbol' and 'strategy'")
        specs[name] = StrategySpec(name=name, **body)
    return specs


def get_spec(name: str, path: str | Path | None = None) -> StrategySpec:
    """Load one spec by name; raise with the available names on a miss."""
    specs = load_specs(path)
    if name not in specs:
        raise KeyError(f"no spec named {name!r}; available: {sorted(specs)}")
    return specs[name]
