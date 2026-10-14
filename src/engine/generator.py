"""Assemble tiered parlays from priced legs."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

from src.api.data_models import BuiltParlay, MarketLeg
from src.engine.correlation import synergy, ticket_conflicts
from src.engine.devig import american_to_decimal, combine_decimal, decimal_to_american, expected_value, format_american
from src.utils.kelly import exposure_pct, kelly_fraction, sizing_warning


@dataclass(frozen=True)
class RiskProfile:
    key: str
    min_legs: int
    max_legs: int
    min_american: int
    max_american: int
    min_leg_prob: float
    kelly_risk: float
    stake_low: float
    stake_high: float
    stake_cap: float | None = None
    per_game: int = 2


PROFILES: dict[str, RiskProfile] = {
    "Conservative": RiskProfile("Conservative", 2, 2, -120, 160, 0.68, 0.25, 0.020, 0.030, per_game=2),
    "Balanced": RiskProfile("Balanced", 3, 4, 250, 500, 0.56, 0.125, 0.010, 0.015, per_game=2),
    "Aggressive": RiskProfile("Aggressive", 4, 6, 600, 1500, 0.42, 0.08, 0.005, 0.005, per_game=2),
    "Moonshot": RiskProfile("Moonshot", 6, 8, 2000, 10000, 0.25, 0.05, 0.001, 0.0025, stake_cap=10.0, per_game=2),
    "Krazy": RiskProfile("Krazy", 4, 8, 9900, 80000, 0.15, 0.04, 0.0005, 0.002, per_game=3),
}


def market_allowed(leg: MarketLeg, profile: RiskProfile) -> bool:
    if profile.key == "Conservative":
        if leg.market_type == "PUCKLINE" and (leg.line or 0) > 0:
            return True
        if leg.market_type == "TEAM_TOTAL" and leg.side == "OVER" and (leg.line or 9) <= 1.5:
            return True
        if leg.market_type == "SOG" and leg.side == "OVER" and (leg.line or 9) <= 2.5:
            return True
        return False
    if profile.key == "Balanced":
        if leg.market_type == "MONEYLINE" and leg.american_odds < 0:
            return True
        if leg.market_type == "TOTAL":
            return True
        if leg.market_type in {"SOG", "POINTS"} and leg.side == "OVER":
            return True
        return False
    if profile.key == "Aggressive":
        if leg.market_type == "PUCKLINE" and (leg.line or 0) < 0:
            return True
        if leg.market_type == "MONEYLINE" and leg.american_odds > 0:
            return True
        if leg.market_type == "GOALSCORER" and leg.side == "YES" and (leg.line or 1) <= 0.5:
            return True
        if leg.market_type == "TEAM_TOTAL" and leg.side == "OVER" and (leg.line or 0) >= 3.5:
            return True
        if leg.market_type == "SAVES" and leg.side == "OVER":
            return True
        return False
    if profile.key == "Moonshot":
        if leg.market_type == "GOALSCORER" and leg.side == "YES":
            return True
        if leg.side in {"OVER", "YES", "HOME", "AWAY"} and leg.american_odds >= -160:
            return True
        return False
    if profile.key == "Krazy":
        if leg.market_type == "GOALSCORER" and leg.side == "YES":
            return True
        if leg.side in {"OVER", "YES", "HOME", "AWAY"} and leg.american_odds >= -150:
            return True
        return False
    return False


def _candidate_pool(legs: list[MarketLeg], profile: RiskProfile) -> tuple[list[MarketLeg], bool]:
    eligible = [leg for leg in legs if leg.selectable and market_allowed(leg, profile)]
    relaxed = False
    floor = profile.min_leg_prob
    pool: list[MarketLeg] = []
    while floor >= profile.min_leg_prob - 0.16:
        pool = [leg for leg in eligible if leg.model_prob + 1e-9 >= floor and leg.ev >= -0.005]
        if len(pool) >= profile.min_legs:
            break
        floor -= 0.04
        relaxed = True
    else:
        pool = [leg for leg in eligible if leg.model_prob + 1e-9 >= profile.min_leg_prob - 0.16]
        relaxed = True
    pool = _trim_pool(pool, profile)
    return pool, relaxed and floor < profile.min_leg_prob


def _trim_pool(pool: list[MarketLeg], profile: RiskProfile) -> list[MarketLeg]:
    """Keep the best edges and the prices that can still multiply into the tier's odds band."""
    ranked = sorted(pool, key=lambda leg: (leg.ev, leg.model_prob), reverse=True)
    targets: list[float] = []
    for count in range(profile.min_legs, profile.max_legs + 1):
        low = american_to_decimal(profile.min_american)
        high = american_to_decimal(profile.max_american)
        targets.append(low ** (1 / count))
        targets.append((low * high) ** (0.5 / count))
    closest = sorted(pool, key=lambda leg: min(abs(leg.decimal_odds - target) for target in targets))
    limit = 14 if profile.key == "Krazy" else 12
    chosen: list[MarketLeg] = []
    seen: set[str] = set()
    counts: dict[str, int] = {}
    for leg in ranked[:10] + closest[:12]:
        if leg.leg_id in seen or counts.get(leg.game_id, 0) >= 3:
            continue
        seen.add(leg.leg_id)
        counts[leg.game_id] = counts.get(leg.game_id, 0) + 1
        chosen.append(leg)
        if len(chosen) >= limit:
            break
    if len(chosen) < profile.min_legs:
        for leg in ranked:
            if leg.leg_id in seen:
                continue
            chosen.append(leg)
            if len(chosen) >= limit:
                break
    return chosen


def _combo_ok(combo: tuple[MarketLeg, ...], profile: RiskProfile) -> bool:
    if ticket_conflicts(list(combo)):
        return False
    per_game: dict[str, int] = {}
    per_player: dict[int, int] = {}
    keys: set[str] = set()
    for leg in combo:
        per_game[leg.game_id] = per_game.get(leg.game_id, 0) + 1
        if per_game[leg.game_id] > profile.per_game:
            return False
        if leg.market_key in keys:
            return False
        keys.add(leg.market_key)
        if leg.player_id:
            per_player[leg.player_id] = per_player.get(leg.player_id, 0) + 1
            if per_player[leg.player_id] > 1:
                return False
    return True


def _band_penalty(american: int, profile: RiskProfile) -> float:
    if profile.min_american <= american <= profile.max_american:
        return 0.0
    if american < profile.min_american:
        return (profile.min_american - american) / 1000.0
    return (american - profile.max_american) / 1000.0


def generate_parlays(
    legs: list[MarketLeg],
    tier: str,
    *,
    bankroll: float,
    stake: float,
    limit: int = 3,
    game_ids: set[str] | None = None,
) -> list[BuiltParlay]:
    profile = PROFILES[tier]
    working = [leg for leg in legs if game_ids is None or leg.game_id in game_ids]
    pool, relaxed = _candidate_pool(working, profile)
    if len(pool) < profile.min_legs:
        return []

    ranked: list[tuple[float, bool, tuple[MarketLeg, ...], int, float, float]] = []
    for count in range(profile.min_legs, profile.max_legs + 1):
        if len(pool) < count:
            continue
        for combo in combinations(pool, count):
            if not _combo_ok(combo, profile):
                continue
            decimal_odds = combine_decimal([leg.decimal_odds for leg in combo])
            american = decimal_to_american(decimal_odds)
            probability = 1.0
            for leg in combo:
                probability *= leg.model_prob
            ev = expected_value(probability, decimal_odds)
            syn_score, _notes = synergy(list(combo))
            in_band = profile.min_american <= american <= profile.max_american
            spread = len({leg.game_id for leg in combo})
            plus_money = sum(1 for leg in combo if leg.american_odds > 0)
            score = ev + syn_score * 0.03 + spread * 0.004 - _band_penalty(american, profile)
            if profile.key in {"Moonshot", "Krazy"}:
                score += plus_money * 0.01
            if not in_band:
                score -= 0.35
            ranked.append((score, in_band, combo, american, decimal_odds, probability))

    if not ranked:
        return []
    ranked.sort(key=lambda item: (item[1], item[0]), reverse=True)
    selected: list[tuple] = []
    for item in ranked:
        combo = item[2]
        ids = {leg.leg_id for leg in combo}
        if any(len(ids & {leg.leg_id for leg in prior[2]}) / len(ids) > 0.5 for prior in selected):
            continue
        selected.append(item)
        if len(selected) >= limit:
            break
    if len(selected) < limit:
        used = {id(item) for item in selected}
        for item in ranked:
            if id(item) in used:
                continue
            selected.append(item)
            if len(selected) >= limit:
                break

    built: list[BuiltParlay] = []
    for _score, in_band, combo, american, decimal_odds, probability in selected:
        built.append(
            _assemble(
                list(combo),
                profile,
                american=american,
                decimal_odds=decimal_odds,
                probability=probability,
                in_band=in_band,
                relaxed=relaxed,
                bankroll=bankroll,
                stake=stake,
            )
        )
    return built


def _assemble(
    legs: list[MarketLeg],
    profile: RiskProfile,
    *,
    american: int,
    decimal_odds: float,
    probability: float,
    in_band: bool,
    relaxed: bool,
    bankroll: float,
    stake: float,
) -> BuiltParlay:
    ev = expected_value(probability, decimal_odds)
    syn_score, notes = synergy(legs)
    fraction = kelly_fraction(decimal_odds, probability, profile.kelly_risk)
    guide_low = bankroll * profile.stake_low
    guide_high = bankroll * profile.stake_high
    kelly_stake = bankroll * fraction
    recommended = kelly_stake if kelly_stake > 0 else guide_low
    recommended = min(recommended, guide_high if guide_high > 0 else recommended)
    if profile.stake_cap is not None:
        recommended = min(recommended, profile.stake_cap)
    recommended = round(max(recommended, 0.0), 2)
    safe_stake = round(max(stake, 0.0), 2)
    payout = round(safe_stake * decimal_odds, 2)
    edge = sum(leg.model_prob - leg.fair_prob for leg in legs) / len(legs)
    summary_bits = list(notes[:3])
    summary_bits.append(
        f"Ticket model probability {probability:.1%} at {format_american(american)} ({ev:+.1%} EV)."
    )
    if profile.key == "Krazy":
        summary_bits.append(f"KRAZY MODE: a $1 stake returns ${decimal_odds:,.0f}.")
    if relaxed:
        summary_bits.append("Probability floor was loosened so the tier could fill a ticket.")
    if len(legs) > len({leg.game_id for leg in legs}):
        summary_bits.append("Same-game legs are multiplied as independent prices. A sportsbook may shorten this combination.")
    if not in_band:
        summary_bits.append(
            f"Combined price sits outside the {format_american(profile.min_american)} to {format_american(profile.max_american)} band; this is the closest mix."
        )
    return BuiltParlay(
        risk_tier=profile.key,
        legs=legs,
        american_odds=american,
        decimal_odds=decimal_odds,
        model_prob=probability,
        ev=ev,
        synergy_score=syn_score,
        synergy_notes=notes,
        rationale_summary=" ".join(summary_bits),
        within_odds_band=in_band,
        filters_relaxed=relaxed,
        recommended_stake=recommended,
        guideline_low=round(guide_low, 2),
        guideline_high=round(guide_high, 2),
        kelly_fraction=fraction,
        exposure_pct=exposure_pct(safe_stake, bankroll),
        exposure_warning=sizing_warning(profile.key, safe_stake, bankroll, recommended),
        stake=safe_stake,
        potential_payout=payout,
        net_profit_if_win=round(payout - safe_stake, 2),
        entry_edge=edge,
    )
