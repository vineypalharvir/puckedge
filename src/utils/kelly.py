"""Fractional Kelly stake sizing."""

from __future__ import annotations


def kelly_fraction(decimal_odds: float, win_prob: float, risk_fraction: float) -> float:
    """Return the bankroll fraction f* = max(0, (b*p - q) / b) * risk fraction."""
    price = decimal_odds - 1.0
    if price <= 0 or win_prob <= 0:
        return 0.0
    lose_prob = 1.0 - win_prob
    full = (price * win_prob - lose_prob) / price
    return max(0.0, full) * risk_fraction


def exposure_pct(wager: float, bankroll: float) -> float:
    if bankroll <= 0:
        return 0.0
    return wager / bankroll


def sizing_warning(tier: str, wager: float, bankroll: float, recommended: float) -> str | None:
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
