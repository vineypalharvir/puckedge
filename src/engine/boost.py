"""Grade a pasted Bet365 Bet Boost against the priced board."""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from src.api.data_models import MarketLeg
from src.engine.correlation import synergy, ticket_conflicts
from src.engine.devig import (
    american_to_decimal,
    american_to_implied,
    combine_decimal,
    decimal_to_american,
    expected_value,
    format_american,
)


class BoostGrade(BaseModel):
    boosted_american: int
    boosted_decimal: float
    implied_prob: float
    matched: list[MarketLeg] = Field(default_factory=list)
    unmatched: list[str] = Field(default_factory=list)
    model_prob: float | None = None
    board_decimal: float | None = None
    board_american: int | None = None
    ev: float | None = None
    lean: str
    summary: str
    notes: list[str] = Field(default_factory=list)
    conflicts: bool = False


def parse_boost_price(text: str) -> tuple[int, float]:
    """Accept American (+650, -110) or decimal (6.50) odds."""
    cleaned = text.strip().replace(",", "").replace(" ", "")
    if not cleaned:
        raise ValueError("Enter the boosted price.")
    if "." in cleaned:
        decimal_odds = float(cleaned)
        if decimal_odds <= 1:
            raise ValueError("Decimal odds have to be above 1.00.")
        return decimal_to_american(decimal_odds), decimal_odds
    american = int(cleaned)
    if american == 0:
        raise ValueError("American odds cannot be 0.")
    return american, american_to_decimal(american)


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


def _numbers(text: str) -> list[float]:
    return [float(match) for match in re.findall(r"[-+]?\d+(?:\.\d+)?", text)]


def _score_line(line: str, leg: MarketLeg) -> int:
    folded = line.lower()
    tokens = _tokens(line)
    selection = leg.selection.lower()
    entity = _tokens(leg.target_entity)
    score = 0
    overlap = tokens & entity
    if overlap:
        score += 4 + len(overlap)
    if tokens & _tokens(leg.matchup):
        score += 1
    numbers = _numbers(line)
    if leg.line is not None and any(abs(number - leg.line) < 0.05 or abs(abs(number) - abs(leg.line)) < 0.05 for number in numbers):
        score += 3
    wants_over = "over" in folded or "o " in folded
    wants_under = "under" in folded or folded.startswith("u ")
    if wants_over and leg.side == "OVER":
        score += 2
    if wants_under and leg.side == "UNDER":
        score += 2
    if any(word in folded for word in ("anytime", "scorer", "to score", "scores")) and leg.market_type == "GOALSCORER":
        if leg.line is not None and leg.line <= 0.5 and "2+" not in folded and "two" not in folded:
            score += 4
        elif leg.line is not None and leg.line >= 1.5 and any(word in folded for word in ("2+", "two", "brace", "multi")):
            score += 4
    if any(word in folded for word in ("shot", "sog")) and leg.market_type == "SOG":
        score += 3
    if "point" in folded and leg.market_type == "POINTS":
        score += 3
    if any(word in folded for word in ("save", "saves")) and leg.market_type == "SAVES":
        score += 3
    if any(word in folded for word in ("moneyline", " ml", "to win", "wins")) and leg.market_type == "MONEYLINE":
        score += 3
    if any(word in folded for word in ("puck", "spread", "-1.5", "+1.5")) and leg.market_type == "PUCKLINE":
        score += 2
    if any(word in folded for word in ("total", "over", "under")) and leg.market_type in {"TOTAL", "TEAM_TOTAL"}:
        score += 1
    if selection and selection in folded:
        score += 6
    return score


def match_boost_lines(lines: list[str], legs: list[MarketLeg]) -> tuple[list[MarketLeg], list[str]]:
    pool = [leg for leg in legs if leg.selectable]
    used: set[str] = set()
    matched: list[MarketLeg] = []
    unmatched: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        ranked = sorted(
            (( _score_line(line, leg), leg) for leg in pool if leg.leg_id not in used),
            key=lambda item: item[0],
            reverse=True,
        )
        if not ranked or ranked[0][0] < 6:
            unmatched.append(line)
            continue
        best_score = ranked[0][0]
        tied = [leg for score, leg in ranked if score == best_score]
        if len(tied) > 1 and len({leg.game_id for leg in tied}) > 1:
            unmatched.append(f"{line} (matches more than one game — add the team or player)")
            continue
        chosen = tied[0]
        used.add(chosen.leg_id)
        matched.append(chosen)
    return matched, unmatched


def grade_boost(price_text: str, paste: str, legs: list[MarketLeg]) -> BoostGrade:
    american, decimal_odds = parse_boost_price(price_text)
    implied = american_to_implied(american)
    lines = [line.strip() for line in paste.splitlines() if line.strip()]
    matched, unmatched = match_boost_lines(lines, legs)
    if not matched:
        return BoostGrade(
            boosted_american=american,
            boosted_decimal=decimal_odds,
            implied_prob=implied,
            unmatched=unmatched or lines,
            lean="NO READ",
            summary="None of those lines matched a leg on today's board. Use a player name, team, and the number (Over 6.5, anytime goal, -1.5).",
        )

    probability = 1.0
    for leg in matched:
        probability *= leg.model_prob
    board_decimal = combine_decimal([leg.decimal_odds for leg in matched])
    ev = expected_value(probability, decimal_odds) if not unmatched else None
    gap = probability - implied
    if unmatched:
        lean = "PARTIAL"
    elif gap >= 0.015:
        lean = "LEAN HIT"
    elif gap <= -0.015:
        lean = "LEAN MISS"
    else:
        lean = "TOSS-UP"
    notes: list[str] = []
    _score, synergy_notes = synergy(matched)
    notes.extend(synergy_notes)
    conflicts = ticket_conflicts(matched)
    if conflicts:
        notes.append("These legs conflict. A book would not leave this combination up as a normal parlay.")
    if len(matched) > len({leg.game_id for leg in matched}):
        notes.append("Same-game legs are priced as if they are independent. The true hit chance can be higher or lower.")
    if board_decimal > 1:
        boost_vs_board = decimal_odds / board_decimal - 1
        if boost_vs_board > 0.02:
            notes.append(f"The boost pays {boost_vs_board:.0%} more than multiplying the board prices.")
        elif boost_vs_board < -0.02:
            notes.append(f"The boost pays {abs(boost_vs_board):.0%} less than the board prices.")
    summary = (
        f"Model cash chance {probability:.1%} on the matched legs. "
        f"The boosted price {format_american(american)} implies {implied:.1%}. "
    )
    if lean == "LEAN HIT":
        summary += "Lean hit: the model thinks this cashes more often than that price."
    elif lean == "LEAN MISS":
        summary += "Lean miss: the model thinks this cashes less often than that price."
    elif lean == "TOSS-UP":
        summary += "Toss-up against the boosted price."
    else:
        summary += "Unmatched lines were left out, so this is not the full boost."
    return BoostGrade(
        boosted_american=american,
        boosted_decimal=decimal_odds,
        implied_prob=implied,
        matched=matched,
        unmatched=unmatched,
        model_prob=probability,
        board_decimal=board_decimal,
        board_american=decimal_to_american(board_decimal),
        ev=ev,
        lean=lean,
        summary=summary,
        notes=notes,
        conflicts=conflicts,
    )
