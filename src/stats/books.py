"""Attach a real posted price to a stat leg. Never invent a book that sent nothing."""

from __future__ import annotations

from src.api.data_models import MarketLeg

SKIPPED_BOOKS = {"synthetic-bet365", "stats", "typed", "from stats"}


def _close(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= 0.2 or abs(left - (right - 0.5)) <= 0.2


def matching_books(
    legs: list[MarketLeg],
    *,
    name: str,
    board_market: str,
    side: str,
    line: float | None,
    preferred: str,
) -> tuple[MarketLeg | None, MarketLeg | None]:
    """Return the sidebar book's price when it posted one, plus the best other real price."""
    wanted = {"over": "OVER", "under": "UNDER", "milestone": "OVER", "anytime": "YES"}.get(side, side.upper())
    needle = name.casefold()
    hits = []
    for leg in legs:
        if leg.market_type != board_market or leg.bookmaker in SKIPPED_BOOKS:
            continue
        if needle not in leg.target_entity.casefold() and leg.target_entity.casefold() not in needle:
            continue
        if leg.side != wanted:
            continue
        if side != "anytime" and not _close(leg.line, line):
            continue
        hits.append(leg)
    if not hits:
        return None, None
    best = max(hits, key=lambda leg: leg.decimal_odds)
    chosen = next((leg for leg in hits if leg.bookmaker == preferred), None)
    return chosen or best, best
