"""Find parlays whose payout matches a stake or a win target."""

from __future__ import annotations

from dataclasses import dataclass

from src.api.data_models import MarketLeg
from src.engine.correlation import ticket_conflicts
from src.engine.devig import combine_decimal, decimal_to_american, expected_value
from src.utils.money import payout_amount, profit_amount
from src.engine.generator import neutralize_synthetic


@dataclass
class TargetTicket:
    sports: list[str]
    legs: list[MarketLeg]
    decimal_odds: float
    american_odds: int
    stake: float
    payout: float
    profit: float
    ev: float
    hit_prob: float
    distance: float


def hunt_parlays(
    pools: dict[str, list[MarketLeg]],
    *,
    stake: float,
    win_target: float | None,
    min_legs: int = 2,
    max_legs: int = 4,
    limit: int = 12,
) -> list[TargetTicket]:
    """Rank tickets by closeness to a win target, or by expected value when no target is set."""
    tagged: list[tuple[str, MarketLeg]] = []
    for sport, legs in pools.items():
        ranked = sorted((leg for leg in neutralize_synthetic(legs) if leg.selectable and leg.ev > -0.02), key=lambda leg: leg.ev, reverse=True)
        tagged.extend((sport, leg) for leg in ranked[:10])
    if len(tagged) < min_legs:
        return []
    target_decimal = None if win_target is None or stake <= 0 else (win_target / stake) + 1.0
    found: list[TargetTicket] = []
    chosen: list[tuple[str, MarketLeg]] = []

    def score_current() -> None:
        if len(chosen) < min_legs:
            return
        markets = [leg for _sport, leg in chosen]
        if ticket_conflicts(markets):
            return
        if len({(sport, leg.game_id, leg.market_key) for sport, leg in chosen}) != len(chosen):
            return
        decimal_odds = combine_decimal([leg.decimal_odds for _sport, leg in chosen])
        probability = 1.0
        for _sport, leg in chosen:
            probability *= leg.model_prob
        payout = payout_amount(stake, decimal_odds)
        profit = profit_amount(stake, decimal_odds)
        distance = 0.0 if target_decimal is None else abs(decimal_odds - target_decimal)
        if target_decimal is not None and distance / target_decimal > 0.35:
            return
        found.append(
            TargetTicket(
                sports=[sport for sport, _leg in chosen],
                legs=markets,
                decimal_odds=decimal_odds,
                american_odds=decimal_to_american(decimal_odds),
                stake=stake,
                payout=payout,
                profit=profit,
                ev=expected_value(probability, decimal_odds),
                hit_prob=probability,
                distance=distance,
            )
        )

    def dfs(start: int) -> None:
        if len(found) > limit * 20:
            return
        if len(chosen) >= min_legs:
            score_current()
        if len(chosen) >= max_legs:
            return
        for index in range(start, len(tagged)):
            sport, leg = tagged[index]
            if any(leg.leg_id == picked.leg_id and sport == picked_sport for picked_sport, picked in chosen):
                continue
            chosen.append((sport, leg))
            dfs(index + 1)
            chosen.pop()

    dfs(0)
    if target_decimal is None:
        found.sort(key=lambda ticket: ticket.ev, reverse=True)
    else:
        found.sort(key=lambda ticket: (ticket.distance, -ticket.ev))
    unique: list[TargetTicket] = []
    seen: set[tuple[str, ...]] = set()
    for ticket in found:
        key = tuple(sorted(leg.leg_id for leg in ticket.legs))
        if key in seen:
            continue
        seen.add(key)
        unique.append(ticket)
        if len(unique) >= limit:
            break
    return unique
