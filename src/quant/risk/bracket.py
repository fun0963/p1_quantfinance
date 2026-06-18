"""Bracket (OCO) exits — automatic stop-loss / take-profit after an entry fills.

Concept borrowed from a production terminal's bracket orders: when a long entry
fills, arm a one-cancels-other pair — a stop-loss below and a take-profit above.
On each bar we check whether price reached either level; the first hit exits the
position and disarms the bracket. Optionally the stop *trails* the high-water
mark, locking in gains as price rises.

Long-only in this phase (matches the strategies). Intrabar tie-break is
conservative: if a single bar's range spans BOTH the stop and the take, the stop
is assumed to fire first (worst case for the trader).
"""
from __future__ import annotations

from dataclasses import dataclass


def bracket_prices(
    entry_price: float, stop_pct: float, take_pct: float
) -> tuple[float | None, float | None]:
    """Absolute (stop, take) prices for a long entry, rounded to cents.

    A pct of 0 disables that leg (returns None). Used to translate a percentage
    bracket into the concrete prices Alpaca's stop_loss / take_profit legs need.
    """
    stop = round(entry_price * (1 - stop_pct), 2) if stop_pct > 0 else None
    take = round(entry_price * (1 + take_pct), 2) if take_pct > 0 else None
    return stop, take


@dataclass
class BracketConfig:
    """Bracket parameters. A pct of 0 disables that leg."""
    stop_pct: float = 0.0      # stop-loss distance below entry, e.g. 0.05 = 5%
    take_pct: float = 0.0      # take-profit distance above entry, e.g. 0.10 = 10%
    trailing: bool = False     # if True, the stop trails the highest price seen

    @property
    def active(self) -> bool:
        return self.stop_pct > 0 or self.take_pct > 0


class Bracket:
    """Armed stop/take levels for one long position; checked once per bar."""

    def __init__(self, entry_price: float, qty: float, cfg: BracketConfig) -> None:
        self.qty = qty
        self.cfg = cfg
        self.entry = entry_price
        self.hwm = entry_price
        self.stop = entry_price * (1 - cfg.stop_pct) if cfg.stop_pct > 0 else None
        self.take = entry_price * (1 + cfg.take_pct) if cfg.take_pct > 0 else None

    def check(self, high: float, low: float) -> tuple[str, float] | None:
        """Update trailing stop and test the bar's range for a hit.

        Returns ("stop-loss"|"take-profit", fill_price) on a trigger, else None.
        """
        # Trailing stop ratchets up with the high-water mark; it never loosens.
        if self.cfg.trailing and self.cfg.stop_pct > 0:
            self.hwm = max(self.hwm, high)
            trailed = self.hwm * (1 - self.cfg.stop_pct)
            self.stop = trailed if self.stop is None else max(self.stop, trailed)

        # Stop checked first (conservative when a bar spans both levels).
        if self.stop is not None and low <= self.stop:
            return ("stop-loss", self.stop)
        if self.take is not None and high >= self.take:
            return ("take-profit", self.take)
        return None
