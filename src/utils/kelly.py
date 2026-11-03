"""Fractional Kelly stake sizing."""

from __future__ import annotations


import math

# Fraction of full Kelly used by each risk tier. Conservative bets a quarter of
# the Kelly stake and Balanced an eighth. The longshot tiers are smaller still
# because their hit chance is the least certain. Per-tier bankroll caps live in
# RiskProfile (stake_low / stake_high / stake_cap) and still apply on top.
QUARTER_KELLY = 0.25
EIGHTH_KELLY = 0.125
TIER_KELLY_FRACTIONS: dict[str, float] = {
    "Conservative": QUARTER_KELLY,
    "Balanced": EIGHTH_KELLY,
    "Aggressive": 0.08,
    "Moonshot": 0.05,
    "Krazy": 0.04,
}

NEEDS_BANKROLL = "Set a bankroll above $0 to see exposure and sizing."
NEEDS_WAGER = "Enter a wager above $0 to see exposure."


def _finite(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def kelly_fraction(decimal_odds: float, win_prob: float, risk_fraction: float) -> float:
    """Return the bankroll fraction f* = max(0, (b*p - q) / b) * risk fraction.

    b = decimal_odds - 1 and q = 1 - p. The result is never negative: a bet with
    no edge (or bad input) stakes nothing.
    """
    odds = _finite(decimal_odds)
    prob = _finite(win_prob)
    risk = _finite(risk_fraction)
    if odds is None or prob is None or risk is None or risk <= 0:
        return 0.0
    price = odds - 1.0
    if price <= 0 or prob <= 0:
        return 0.0
    prob = min(prob, 1.0)
    lose_prob = 1.0 - prob
    full = (price * prob - lose_prob) / price
    return max(0.0, full) * risk


def exposure_pct(wager: float, bankroll: float) -> float:
    """Wager as a fraction of bankroll (0.05 = 5%). Never negative.

    A wager or bankroll at or below zero has no meaningful exposure, so it is 0.0
    rather than a negative or infinite number. Use exposure_message() to tell the
    user why.
    """
    stake = _finite(wager)
    bank = _finite(bankroll)
    if stake is None or bank is None or bank <= 0 or stake <= 0:
        return 0.0
    return stake / bank


def exposure_message(wager: float, bankroll: float) -> str | None:
    """A plain-language reason when exposure cannot be computed, else None."""
    bank = _finite(bankroll)
    stake = _finite(wager)
    if bank is None or bank <= 0:
        return NEEDS_BANKROLL
    if stake is None or stake <= 0:
        return NEEDS_WAGER
    return None


def exposure_text(wager: float, bankroll: float) -> str:
    """'5.0%' for a valid wager and bankroll, otherwise an em dash."""
    if exposure_message(wager, bankroll) is not None:
        return "—"
    return f"{exposure_pct(wager, bankroll) * 100:.1f}%"


def sizing_warning(tier: str, wager: float, bankroll: float, recommended: float) -> str | None:
    problem = exposure_message(wager, bankroll)
    if problem is not None:
        return problem if _finite(bankroll) is None or float(bankroll) <= 0 else None
    pct = exposure_pct(wager, bankroll)
    if tier in {"Aggressive", "Moonshot", "Krazy"} and pct > 0.05:
        return (
            f"This wager is {pct * 100:.1f}% of bankroll. "
            f"{tier} tickets are sized at a fractional Kelly cap. "
            f"Suggested stake: ${recommended:,.2f}."
        )
    if pct > 0.05:
        return (
            f"This wager is {pct * 100:.1f}% of bankroll, above a 5% exposure flag. "
            f"Suggested stake: ${recommended:,.2f}."
        )
    return None
