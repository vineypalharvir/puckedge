"""Grade a pasted Bet365 Bet Boost against the priced board."""

from __future__ import annotations

import hashlib
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
    if any(word in folded for word in ("anytime", "scorer", "to score", "scores", " td", "touchdown")) and leg.market_type in {"GOALSCORER", "ANYTIME_TD"}:
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
    if any(word in folded for word in ("moneyline", "money line", " ml", "to win", "wins")) and leg.market_type == "MONEYLINE":
        score += 3
    if any(word in folded for word in ("puck", "spread", "-1.5", "+1.5")) and leg.market_type == "PUCKLINE":
        score += 2
    if any(word in folded for word in ("total", "over", "under")) and leg.market_type in {"TOTAL", "TEAM_TOTAL"}:
        score += 1
    if selection and selection in folded:
        score += 6
    return score


def _flat(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def manual_leg(description: str, price_text: str) -> MarketLeg:
    """A leg the odds feed did not have. The hit chance is the price the user typed."""
    label = " ".join(description.split())
    if not label:
        raise ValueError("Enter the leg.")
    american, decimal_odds = parse_boost_price(price_text)
    implied = american_to_implied(american)
    flat = _flat(label)
    if any(phrase in flat for phrase in ("score a td", "anytime td", "touchdown", "to score")):
        market = "ANYTIME_TD"
    elif "money line" in flat or "moneyline" in flat:
        market = "MONEYLINE"
    else:
        market = "PROP"
    digest = hashlib.sha1(f"{label}|{american}".encode()).hexdigest()[:16]
    return MarketLeg(
        leg_id=f"typed-{digest}",
        game_id=f"typed-{digest}",
        matchup="Typed odds",
        game_date="",
        start_time_utc="",
        market_type=market,
        market_key=f"typed|{digest}",
        target_entity=label,
        selection=label,
        side="YES",
        american_odds=american,
        decimal_odds=decimal_odds,
        implied_prob=implied,
        fair_prob=implied,
        model_prob=implied,
        ev=0.0,
        bookmaker="typed",
        rationale="No feed had this leg. The hit chance is the odds you typed.",
    )


def _noise_line(line: str) -> bool:
    flat = _flat(line)
    if not flat:
        return True
    if flat in {"money line", "moneyline", "anytime td scorer", "same game parlay", "early payout"}:
        return True
    if re.fullmatch(r"(mon|tue|wed|thu|fri|sat|sun)\s+\w+\s+\d{1,2}", flat):
        return True
    if re.fullmatch(r"\d{1,2}\s+\d{2}\s*(am|pm)?", flat):
        return True
    if any(word in flat for word in ("cash out", "reuse selections", "to return", "wager", "early payout")):
        return True
    return False


def is_player_line(line: str) -> bool:
    flat = _flat(line)
    if "money" in flat or flat.endswith(" ml"):
        return False
    return any(word in flat for word in ("td", "touchdown", "score", "over", "under", "yard", "point", "rebound", "assist", "goal", "anytime"))


def is_context_line(line: str) -> bool:
    """Team names under a bet, such as the opponent, are the matchup, not a separate leg."""
    flat = _flat(line)
    if not flat or _noise_line(line):
        return True
    if any(word in flat for word in ("money", "td", "touchdown", "score", "over", "under", "yard", "point", "rebound", "assist", "goal", "anytime", "ml")):
        return False
    return True


def _header_odds(line: str) -> str | None:
    flat = _flat(line)
    if "parlay" not in flat and "same game" not in flat:
        return None
    found = re.findall(r"\d+\.\d+|\+\d+|-\d+", line.replace(",", ""))
    return found[-1] if found else None


def _team_price(line: str) -> tuple[str, str] | None:
    match = re.search(r"(.+?)\s+(\d+\.\d{2,}|\+\d+|-\d+)\s*$", line.strip())
    if not match:
        return None
    label = match.group(1).strip(" -")
    if _noise_line(label) or len(_flat(label)) < 3:
        return None
    return label, match.group(2)


def split_bet_slip(text: str) -> list[dict]:
    """Turn a sportsbook bet list into groups of legs and the odds printed on each bet."""
    groups: list[dict] = []
    current = {"odds": None, "lines": []}

    def flush() -> None:
        if current["lines"]:
            groups.append({"odds": current["odds"], "lines": list(current["lines"])})

    for raw in text.splitlines():
        line = raw.strip()
        if not line or _noise_line(line):
            continue
        header = _header_odds(line)
        if header:
            flush()
            current = {"odds": header, "lines": []}
            continue
        priced = _team_price(line)
        if priced:
            label, odds = priced
            if not current["lines"] and current["odds"] is None:
                current["odds"] = odds
            current["lines"].append(f"{label} money line")
            continue
        current["lines"].append(line)
    flush()
    return groups


def _slip_score(line: str, leg: MarketLeg) -> int:
    flat = _flat(line)
    words = set(flat.split())
    if not words:
        return 0
    score = 0
    name = _flat(leg.target_entity)
    name_words = [word for word in name.split() if len(word) > 1]
    last = name_words[-1] if name_words else ""
    if last and last in words:
        score += 8
    if name and name in flat:
        score += 6
    abbrevs = [word.lower() for word in re.findall(r"[A-Za-z]{2,4}", leg.matchup)]
    if any(abbrev in words for abbrev in abbrevs):
        score += 5
    td = any(phrase in flat for phrase in ("score a td", "anytime td", "to score", "touchdown"))
    if td and leg.market_type in {"ANYTIME_TD", "GOALSCORER"} and last in words:
        score += 6
    moneyline = "money line" in flat or "moneyline" in flat
    team_hit = (last in words) or any(abbrev in words for abbrev in abbrevs)
    if moneyline and leg.market_type == "MONEYLINE" and team_hit:
        score += 6
    if not td and not moneyline and leg.market_type == "MONEYLINE" and team_hit and "over" not in flat and "under" not in flat:
        score += 3
    return score


def match_slip_line(line: str, legs: list[MarketLeg], used: set[str]) -> MarketLeg | None:
    ranked = sorted(
        (( _slip_score(line, leg), leg) for leg in legs if leg.selectable and leg.leg_id not in used),
        key=lambda item: item[0],
        reverse=True,
    )
    if not ranked or ranked[0][0] < 8:
        return None
    best = ranked[0][0]
    tied = [leg for score, leg in ranked if score == best]
    if len({leg.leg_id for leg in tied}) > 1 and len({leg.game_id for leg in tied}) > 1:
        moneylines = [leg for leg in tied if leg.market_type == "MONEYLINE"]
        if len(moneylines) == 1:
            return moneylines[0]
        return None
    moneylines = [leg for leg in tied if leg.market_type == "MONEYLINE"]
    if moneylines and "td" not in _flat(line) and "over" not in _flat(line):
        return moneylines[0]
    return tied[0]


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
