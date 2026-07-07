"""CostModel: cost arithmetic and calibration from a TCA report."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from quant.backtest.costs import CostModel


def test_defaults_and_bps_arithmetic():
    cm = CostModel()  # 5 bps fees + 5 bps slippage
    assert cm.per_side_bps == pytest.approx(10.0)
    assert cm.roundtrip_bps == pytest.approx(20.0)


def test_negative_cost_rejected():
    with pytest.raises(ValueError):
        CostModel(slippage=-0.001)


def test_summary_reports_bps():
    s = CostModel(fees=0.0003, slippage=0.0007).summary()
    assert "3.0 bps" in s and "7.0 bps" in s and "10.0 bps/side" in s


def _report(notional, commission, avg_slip_bps):
    return SimpleNamespace(total_notional_usd=notional, total_commission_usd=commission,
                           avg_slippage_bps=avg_slip_bps)


def test_from_tca_calibrates_fees_and_slippage():
    # commission 100 / 1,000,000 notional = 1 bps; measured slippage 8 bps.
    cm = CostModel.from_tca(_report(1_000_000, 100.0, 8.0))
    assert cm.fees == pytest.approx(1e-4)       # 1 bps
    assert cm.slippage == pytest.approx(8e-4)   # 8 bps


def test_from_tca_floors_negative_slippage_at_zero():
    # Average price improvement must not become a backtest bonus.
    cm = CostModel.from_tca(_report(1_000_000, 0.0, -5.0))
    assert cm.slippage == 0.0


def test_from_tca_falls_back_to_defaults_without_notional():
    cm = CostModel.from_tca(_report(0.0, 0.0, float("nan")))
    assert cm == CostModel()  # nothing to calibrate against
