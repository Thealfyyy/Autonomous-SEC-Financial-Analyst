"""Deterministic Python financial calculator (zero LLM arithmetic)."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pydantic import BaseModel


class MetricResult(BaseModel):
    """Structured output from a deterministic financial calculation."""

    name: str
    formula: str
    inputs: dict[str, str]
    value: Decimal
    unit: str
    formatted: str


def _to_decimal(val: float | str | Decimal, label: str) -> Decimal:
    try:
        d = Decimal(str(val))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"Invalid numeric input for '{label}': {val}") from exc
    if not d.is_finite():
        raise ValueError(f"Non-finite numeric input for '{label}': {val}")
    return d


def _round_dec(val: Decimal, places: int = 4) -> Decimal:
    quant = Decimal(1).scaleb(-places)
    return val.quantize(quant, rounding=ROUND_HALF_UP)


def yoy_growth(
    current: float | str | Decimal,
    previous: float | str | Decimal,
    metric_name: str = "yoy_growth",
) -> MetricResult:
    """Compute Year-over-Year percentage growth: (current - previous) / abs(previous)."""
    curr = _to_decimal(current, "current")
    prev = _to_decimal(previous, "previous")
    if prev == 0:
        raise ValueError("Cannot compute YoY growth when previous value is zero.")

    pct = _round_dec(((curr - prev) / abs(prev)) * Decimal(100), places=2)
    return MetricResult(
        name=metric_name,
        formula="(current - previous) / abs(previous) * 100",
        inputs={"current": str(curr), "previous": str(prev)},
        value=pct,
        unit="percent",
        formatted=f"{pct:+}%",
    )


def margin(
    numerator: float | str | Decimal,
    denominator: float | str | Decimal,
    metric_name: str = "margin",
) -> MetricResult:
    """Compute a percentage margin: (numerator / denominator) * 100."""
    num = _to_decimal(numerator, "numerator")
    den = _to_decimal(denominator, "denominator")
    if den == 0:
        raise ValueError("Cannot compute margin when denominator is zero.")

    pct = _round_dec((num / den) * Decimal(100), places=2)
    return MetricResult(
        name=metric_name,
        formula="(numerator / denominator) * 100",
        inputs={"numerator": str(num), "denominator": str(den)},
        value=pct,
        unit="percent",
        formatted=f"{pct}%",
    )


def cagr(
    start_value: float | str | Decimal,
    end_value: float | str | Decimal,
    periods: int,
    metric_name: str = "cagr",
) -> MetricResult:
    """Compute Compound Annual Growth Rate over `periods` years."""
    start = _to_decimal(start_value, "start_value")
    end = _to_decimal(end_value, "end_value")
    if periods <= 0:
        raise ValueError("periods must be a positive integer.")
    if start <= 0:
        raise ValueError("start_value must be positive to compute CAGR.")
    if end < 0:
        raise ValueError("end_value cannot be negative to compute CAGR.")

    rate = (float(end / start) ** (1.0 / periods) - 1.0) * 100.0
    pct = _round_dec(Decimal(str(rate)), places=2)
    return MetricResult(
        name=metric_name,
        formula="((end_value / start_value) ** (1 / periods) - 1) * 100",
        inputs={"start_value": str(start), "end_value": str(end), "periods": str(periods)},
        value=pct,
        unit="percent",
        formatted=f"{pct}%",
    )


def ratio(
    numerator: float | str | Decimal,
    denominator: float | str | Decimal,
    metric_name: str = "ratio",
) -> MetricResult:
    """Compute a dimensionless financial ratio: numerator / denominator."""
    num = _to_decimal(numerator, "numerator")
    den = _to_decimal(denominator, "denominator")
    if den == 0:
        raise ValueError("Cannot compute ratio when denominator is zero.")

    val = _round_dec(num / den, places=4)
    return MetricResult(
        name=metric_name,
        formula="numerator / denominator",
        inputs={"numerator": str(num), "denominator": str(den)},
        value=val,
        unit="ratio",
        formatted=f"{val}x",
    )


def free_cash_flow(
    operating_cash_flow: float | str | Decimal,
    capex: float | str | Decimal,
    unit: str = "USD",
) -> MetricResult:
    """Compute Free Cash Flow: operating_cash_flow - abs(capex)."""
    ocf = _to_decimal(operating_cash_flow, "operating_cash_flow")
    capex_abs = abs(_to_decimal(capex, "capex"))
    fcf = ocf - capex_abs
    return MetricResult(
        name="free_cash_flow",
        formula="operating_cash_flow - abs(capex)",
        inputs={"operating_cash_flow": str(ocf), "capex": str(capex_abs)},
        value=fcf,
        unit=unit,
        formatted=f"{fcf:,} {unit}",
    )
