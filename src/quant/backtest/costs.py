"""Transaction-cost model for backtests — the knob that stops a backtest lying.

A backtest that charges no cost systematically overstates returns. `CostModel`
bundles the two per-side costs — commission and adverse slippage, both as
fractions of notional — that the engines apply on every fill. Defaults are a
conservative retail estimate; the honest move is to *calibrate* the model from
what live execution actually paid via `from_tca()`, closing the loop:

    live orders --(TCA)--> measured slippage/commission --(CostModel)--> backtest

Kept dependency-free (no ops import) so it stays in the backtest layer;
`from_tca` duck-types the report it is handed by the caller.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CostModel:
    """Per-side backtest costs as fractions of notional.

    fees:     commission charged on each fill's notional (e.g. 0.0005 = 5 bps).
    slippage: adverse price move on each fill (pay up to buy / receive less to
              sell), same units. Applied by both engines to every trade.
    """
    fees: float = 0.0005      # 5 bps commission
    slippage: float = 0.0005  # 5 bps slippage

    def __post_init__(self) -> None:
        if self.fees < 0 or self.slippage < 0:
            raise ValueError("cost model fees/slippage must be non-negative")

    @property
    def per_side_bps(self) -> float:
        """All-in cost charged on one fill, in basis points of notional."""
        return (self.fees + self.slippage) * 1e4

    @property
    def roundtrip_bps(self) -> float:
        """Approximate cost of a full entry+exit round trip, in basis points."""
        return self.per_side_bps * 2

    @classmethod
    def from_tca(cls, report: Any, *, floor_slippage_bps: float = 0.0) -> CostModel:
        """Calibrate a cost model from a live TCA report so the backtest charges
        what execution actually cost.

        Commission rate is the realized commission over filled notional; slippage
        is the measured average adverse slippage. Negative average slippage (price
        improvement) is not modeled as a backtest *bonus* — it is floored at
        `floor_slippage_bps` (default 0) to stay conservative. Falls back to the
        class defaults when the report has no filled notional to calibrate against.
        """
        notional = getattr(report, "total_notional_usd", 0.0) or 0.0
        if notional <= 0:
            return cls()

        commission = getattr(report, "total_commission_usd", 0.0) or 0.0
        fees = commission / notional

        avg_slip_bps = getattr(report, "avg_slippage_bps", float("nan"))
        if avg_slip_bps is None or math.isnan(avg_slip_bps):
            avg_slip_bps = 0.0
        slippage = max(avg_slip_bps, floor_slippage_bps) / 1e4
        return cls(fees=max(fees, 0.0), slippage=max(slippage, 0.0))

    def summary(self) -> str:
        return (f"cost model: fees {self.fees * 1e4:.1f} bps + slippage "
                f"{self.slippage * 1e4:.1f} bps = {self.per_side_bps:.1f} bps/side "
                f"({self.roundtrip_bps:.1f} bps round trip)")
