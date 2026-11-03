"""Money math rounded to cents the same way everywhere (half up, no float drift)."""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")


def _dec(value: object) -> Decimal:
    try:
        number = Decimal(repr(float(value)))  # type: ignore[arg-type]
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)
    return number if number.is_finite() else Decimal(0)


def to_cents(value: object) -> float:
    """Round a dollar amount to 2 decimals, half up. Non-finite input becomes 0.0."""
    return float(_dec(value).quantize(CENT, rounding=ROUND_HALF_UP))


def payout_amount(stake: object, decimal_odds: object) -> float:
    """Total return (stake included) = stake x combined decimal odds."""
    return float((_dec(stake) * _dec(decimal_odds)).quantize(CENT, rounding=ROUND_HALF_UP))


def profit_amount(stake: object, decimal_odds: object) -> float:
    """Profit = payout - stake, computed from the already-rounded payout."""
    stake_cents = _dec(stake).quantize(CENT, rounding=ROUND_HALF_UP)
    payout = (_dec(stake) * _dec(decimal_odds)).quantize(CENT, rounding=ROUND_HALF_UP)
    return float(payout - stake_cents)


def format_money(value: object) -> str:
    """$1,234.50 or -$1,234.50, rounded half up."""
    cents = _dec(to_cents(value))
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents):,.2f}"


def is_positive_amount(value: object) -> bool:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0
