"""American odds, implied probability, devigging, and expected value."""

from __future__ import annotations

import math
from decimal import Decimal


def is_valid_american(american: object) -> bool:
    """A real American price is a finite number with |odds| >= 100.

    Anything between -100 and +100 (including 0), None, NaN and infinity is not
    a price any sportsbook posts, so it is rejected rather than converted.
    """
    if american is None or isinstance(american, bool):
        return False
    try:
        value = float(american)
    except (TypeError, ValueError):
        return False
    return math.isfinite(value) and abs(value) >= 100.0


def _require_american(american: object) -> float:
    if not is_valid_american(american):
        raise ValueError(f"Invalid American odds: {american!r}. Prices must be +100 or higher, or -100 or lower.")
    return float(american)  # type: ignore[arg-type]


def american_to_decimal(american: int) -> float:
    """A >= +100: 1 + A/100. A <= -100: 1 + 100/|A|."""
    value = _require_american(american)
    if value > 0:
        return 1.0 + value / 100.0
    return 1.0 + 100.0 / abs(value)


def decimal_to_american(decimal_odds: float) -> int:
    """Dec >= 2.0: +round((Dec-1)*100). Dec < 2.0: -round(100/(Dec-1)).

    Decimals at or below 1.0 (or non-finite) cannot pay anything, so they map to
    the shortest price the app uses (-10000) instead of raising mid-render.
    """
    try:
        decimal_odds = float(decimal_odds)
    except (TypeError, ValueError):
        return -10000
    if not math.isfinite(decimal_odds) or decimal_odds <= 1.0:
        return -10000
    if decimal_odds >= 2.0:
        american = int(math.floor((decimal_odds - 1.0) * 100 + 0.5))
        return max(american, 100)
    american = -int(math.floor(100.0 / (decimal_odds - 1.0) + 0.5))
    if american > -100:
        return -101
    return american


def american_to_implied(american: int) -> float:
    value = _require_american(american)
    if value > 0:
        return 100.0 / (value + 100.0)
    return abs(value) / (abs(value) + 100.0)


def implied_to_american(probability: float) -> int:
    probability = min(max(probability, 0.01), 0.99)
    return decimal_to_american(1.0 / probability)


def multiplicative_devig(implied: list[float]) -> list[float]:
    total = sum(implied)
    if total <= 0:
        raise ValueError("Implied probabilities must be positive")
    return [p / total for p in implied]


def power_devig(implied: list[float]) -> list[float]:
    """Find k such that the powered prices sum to 1."""
    if any(p <= 0 for p in implied):
        raise ValueError("Implied probabilities must be positive")
    low, high = 0.05, 8.0
    for _ in range(48):
        mid = (low + high) / 2.0
        total = sum(p**mid for p in implied)
        if total > 1.0:
            low = mid
        else:
            high = mid
    exponent = (low + high) / 2.0
    fair = [p**exponent for p in implied]
    normalizer = sum(fair)
    return [p / normalizer for p in fair]


def expected_value(model_prob: float, decimal_odds: float, push_prob: float = 0.0) -> float:
    """Stake-denominated EV. Pushes return the stake, so they are not losses.

    When push_prob is 0 this is the spec identity (P * decimal) - 1.
    """
    win = min(max(model_prob, 0.0), 1.0)
    push = min(max(push_prob, 0.0), 1.0 - win)
    loss = max(0.0, 1.0 - win - push)
    return win * (decimal_odds - 1.0) - loss


def combine_decimal(decimals: list[float]) -> float:
    """Parlay price: the product of every leg's decimal odds.

    Pushed or voided legs are passed in as 1.0, which removes their multiplier.
    """
    total = Decimal(1)
    for price in decimals:
        total *= Decimal(repr(float(price)))
    return float(total)


def format_american(american: int | None) -> str:
    if american is None or isinstance(american, bool):
        return "—"
    try:
        number = float(american)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(number):
        return "—"
    value = int(round(number))
    if value > 0:
        return f"+{value}"
    return str(value)


def poisson_pmf(lam: float, n: int = 14) -> list[float]:
    lam = max(float(lam), 0.05)
    n = max(int(n), 1)
    probs = [0.0] * n
    # Start at the mode so a save total around 30 does not underflow.
    mode = min(int(lam), n - 1)
    probs[mode] = 1.0
    for k in range(mode, 0, -1):
        probs[k - 1] = probs[k] * k / lam
    for k in range(mode, n - 1):
        probs[k + 1] = probs[k] * lam / (k + 1)
    scale = math.exp(-lam) * (lam**mode) / math.factorial(mode)
    # The relative series is scaled back onto the real Poisson mass.
    # factorial(mode) is exact for the lambdas we use (goals and saves).
    total = sum(probs)
    if total <= 0 or scale <= 0:
        return [1.0] + [0.0] * (n - 1)
    return [prob * scale for prob in probs]


def poisson_sf_over(lam: float, line: float) -> tuple[float, float]:
    """Return (P(win over), P(push)) for an over at `line`."""
    lam = max(float(lam), 0.05)
    n = max(int(math.floor(line)) + 3, int(lam + 8 * math.sqrt(lam)) + 3, 12)
    n = min(n, 90)
    probs = poisson_pmf(lam, n)
    win = 0.0
    push = 0.0
    for goals, prob in enumerate(probs):
        if goals > line + 1e-9:
            win += prob
        elif abs(goals - line) < 1e-9:
            push += prob
    tail = max(0.0, 1.0 - sum(probs))
    if (n - 1) > line:
        win += tail
    return min(max(win, 0.0), 1.0), min(max(push, 0.0), 1.0)
