"""Offline unit tests for deterministic financial math calculator."""

from decimal import Decimal

import pytest

from sec_analyst.tools.financial_math import cagr, free_cash_flow, margin, ratio, yoy_growth


def test_financial_math_calculations() -> None:
    yoy = yoy_growth(60_922_000_000, 26_974_000_000, metric_name="revenue_yoy")
    assert yoy.value == Decimal("125.85")
    assert yoy.formatted == "+125.85%"

    gm = margin(44_301, 60_922, metric_name="gross_margin")
    assert gm.value == Decimal("72.72")
    assert gm.formatted == "72.72%"

    cagr_res = cagr(100, 144, periods=2)
    assert cagr_res.value == Decimal("20.00")

    r = ratio(150, 100, metric_name="current_ratio")
    assert r.value == Decimal("1.5000")

    fcf = free_cash_flow(28_090_000_000, -1_069_000_000)
    assert fcf.value == Decimal(27021000000)


def test_financial_math_edge_case_guards() -> None:
    with pytest.raises(ValueError, match="zero"):
        yoy_growth(100, 0)
    with pytest.raises(ValueError, match="zero"):
        margin(50, 0)
    with pytest.raises(ValueError, match="positive"):
        cagr(-10, 100, 2)
