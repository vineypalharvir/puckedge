"""Grade legs from a final box score and roll a ticket up."""

from __future__ import annotations

import re
from types import SimpleNamespace

from src.api.data_models import BoxScore
from src.engine.devig import american_to_decimal, combine_decimal, decimal_to_american
from src.utils.money import profit_amount, to_cents


def _threshold(actual: float, line: float, side: str) -> str:
    if side in {"OVER", "YES"}:
        if actual > line + 1e-9:
            return "WON"
        if abs(actual - line) < 1e-9:
            return "PUSH"
        return "LOST"
    if actual < line - 1e-9:
        return "WON"
    if abs(actual - line) < 1e-9:
        return "PUSH"
    return "LOST"


def _find_skater(box: BoxScore, player_id: int | None, name: str):
    if player_id:
        for skater in box.skaters:
            if skater.player_id == player_id:
                return skater
    last = name.split()[-1].casefold() if name else ""
    matches = [skater for skater in box.skaters if last and last in skater.name.casefold()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1 and name:
        # Two players share a last name: narrow by first initial (NHL box scores say "H. Lindholm").
        initial = name.split()[0][:1].casefold()
        narrowed = [s for s in matches if s.name.strip()[:1].casefold() == initial]
        if len(narrowed) == 1:
            return narrowed[0]
    return None


def _name_matches(box: BoxScore, name: str) -> int:
    last = name.split()[-1].casefold() if name else ""
    if not last:
        return 0
    return sum(1 for skater in box.skaters if last in skater.name.casefold())


def _find_goalie(box: BoxScore, player_id: int | None, name: str):
    if player_id:
        for goalie in box.goalies:
            if goalie.player_id == player_id:
                return goalie
    last = name.split()[-1].casefold() if name else ""
    matches = [goalie for goalie in box.goalies if last and last in goalie.name.casefold()]
    if len(matches) == 1:
        return matches[0]
    starters = [goalie for goalie in matches if goalie.starter]
    return starters[0] if starters else (matches[0] if matches else None)


_CUSTOM_STATS = {
    "AST": "ASSISTS",
    "ASSIST": "ASSISTS",
    "ASSISTS": "ASSISTS",
    "PTS": "POINTS",
    "POINT": "POINTS",
    "POINTS": "POINTS",
    "REB": "REBOUNDS",
    "REBS": "REBOUNDS",
    "REBOUNDS": "REBOUNDS",
    "3PM": "THREES",
    "3PT": "THREES",
    "3S": "THREES",
    "THREES": "THREES",
    "SOG": "SOG",
    "SHOTS": "SOG",
    "GOALS": "GOALSCORER",
    "SAVES": "SAVES",
}
_OVER_UNDER = re.compile(r"^(?P<name>.+?)\s+(?P<side>over|under)\s+(?P<line>\d+(?:\.\d+)?)\s+(?P<stat>[\w]+)\s*$", re.I)
_PLUS = re.compile(r"^(?P<name>.+?)\s+(?P<line>\d+(?:\.\d+)?)\+\s+(?P<stat>[\w]+)\s*$", re.I)


def parse_custom_selection(selection: str | None):
    """'Rasmus Andersson Under 0.5 Ast' -> (market, side, line); '25+ Points' -> OVER 24.5."""
    text = (selection or "").strip()
    match = _OVER_UNDER.match(text)
    if match:
        market = _CUSTOM_STATS.get(match["stat"].upper())
        if market:
            return market, match["side"].upper(), float(match["line"])
    match = _PLUS.match(text)
    if match:
        market = _CUSTOM_STATS.get(match["stat"].upper())
        if market:
            return market, "OVER", float(match["line"]) - 0.5
    return None


def grade_leg(leg, box: BoxScore | None) -> tuple[str, float | None, str]:
    if box is None or not box.is_final:
        state = box.game_state if box is not None else "missing"
        return "PENDING", None, f"Game is {state}."

    if leg.market_type == "CUSTOM":
        parsed = parse_custom_selection(getattr(leg, "selection", None))
        if parsed is None:
            return "PENDING", None, f"Could not read the custom pick '{getattr(leg, 'selection', '')}', so it has to be settled by hand."
        market, side, line = parsed
        leg = SimpleNamespace(
            market_type=market,
            side=side,
            line=line,
            player_id=getattr(leg, "player_id", None),
            target_entity=leg.target_entity,
            team_abbrev=getattr(leg, "team_abbrev", None),
        )

    market = leg.market_type
    side = leg.side
    line = leg.line
    home = box.home_score
    away = box.away_score

    if market in {"MONEYLINE", "MONEYLINE_REG"}:
        if market == "MONEYLINE_REG" and box.went_beyond_regulation:
            won = side == "DRAW"
            return ("WON" if won else "LOST"), float(home - away), "Decided beyond regulation."
        if home == away and market == "MONEYLINE":
            return "PUSH", 0.0, "Final score was tied."
        winner = "HOME" if home > away else "AWAY"
        if market == "MONEYLINE_REG" and home == away:
            winner = "DRAW"
        actual_side = side
        if leg.team_abbrev == box.home_abbrev:
            actual_side = "HOME"
        elif leg.team_abbrev == box.away_abbrev:
            actual_side = "AWAY"
        won = actual_side == winner
        note = f"Final {box.away_abbrev} {away}, {box.home_abbrev} {home}."
        return ("WON" if won else "LOST"), float(home - away), note

    if market in {"PUCKLINE", "SPREAD"} and line is not None:
        if leg.team_abbrev == box.home_abbrev or side == "HOME":
            margin = home - away
            label = box.home_abbrev
        else:
            margin = away - home
            label = box.away_abbrev
        adjusted = margin + line
        if adjusted > 1e-9:
            status = "WON"
        elif abs(adjusted) <= 1e-9:
            status = "PUSH"
        else:
            status = "LOST"
        return status, float(margin), f"{label} margin {margin:+.0f} against {line:+.1f}."

    if market == "TOTAL" and line is not None:
        total = home + away
        status = _threshold(total, line, side)
        return status, float(total), f"Combined goals {total} vs {side.title()} {line:.1f}."

    if market == "TEAM_TOTAL" and line is not None:
        if leg.team_abbrev == box.home_abbrev:
            goals = home
            label = box.home_abbrev
        else:
            goals = away
            label = box.away_abbrev
        status = _threshold(goals, line, side)
        return status, float(goals), f"{label} scored {goals} vs {side.title()} {line:.1f}."

    counted = {
        "SOG": "sog",
        "POINTS": "points",
        "GOALSCORER": "goals",
        "ANYTIME_TD": "ANYTIME_TD",
        "PASS_YDS": "PASS_YDS",
        "RUSH_YDS": "RUSH_YDS",
        "REC_YDS": "REC_YDS",
        "REBOUNDS": "REBOUNDS",
        "ASSISTS": "ASSISTS",
        "THREES": "THREES",
    }
    if market in counted:
        skater = _find_skater(box, leg.player_id, leg.target_entity)
        if skater is None:
            if _name_matches(box, leg.target_entity) > 1:
                return "PENDING", None, "More than one player matches that name, so the leg needs a manual check."
            return "VOID", None, "Player was not in the final box score, so the leg is void."
        key = counted[market]
        if key == "sog":
            actual = float(skater.sog)
        elif key == "points":
            actual = float(skater.points)
        elif key == "goals":
            actual = float(skater.goals)
        elif key == "ASSISTS" and key not in skater.stats:
            actual = float(skater.assists)
        elif key in skater.stats:
            actual = float(skater.stats[key])
        elif key in {"PASS_YDS", "RUSH_YDS", "REC_YDS", "ANYTIME_TD"}:
            # The player is in the box score but has no row for this category: zero.
            actual = 0.0
        else:
            return "VOID", None, "That stat was not in the final box score, so the leg is void."
        label = key.replace("_", " ").lower()
        status = _threshold(actual, line if line is not None else 0.5, side if side else "YES")
        return status, float(actual), f"{skater.name} finished with {actual} {label}."

    if market == "SAVES":
        goalie = _find_goalie(box, leg.player_id, leg.target_entity)
        if goalie is None:
            return "VOID", None, "Goalie was not in the final box score, so the leg is void."
        status = _threshold(goalie.saves, line if line is not None else 0.0, side or "OVER")
        return status, float(goalie.saves), f"{goalie.name} made {goalie.saves} saves."

    # An unknown market is not a void: voiding it would drop a possibly losing leg
    # from the ticket. Leave it pending so it is never settled by guesswork.
    return "PENDING", None, f"No automatic grader for market {market}; settle it manually."


def rollup_ticket(legs, stake: float) -> tuple[str, float, float | None, int | None]:
    """Settle a ticket from its leg statuses.

    Returns (status, net profit, settled decimal odds, settled American odds).
    A pushed or voided leg counts as decimal 1.0, so its multiplier is removed and
    the payout is recomputed on the remaining winning legs. Only a LOST leg loses
    the ticket. If every leg pushed or voided the stake is refunded (PUSHED).
    """
    statuses = [leg.leg_status for leg in legs]
    if any(status == "LOST" for status in statuses):
        return "LOST", to_cents(-float(stake)), None, None
    if any(status == "PENDING" for status in statuses):
        return "PENDING", 0.0, None, None
    won = [leg for leg in legs if leg.leg_status == "WON"]
    if not won:
        return "PUSHED", 0.0, 1.0, None
    decimal_odds = combine_decimal([american_to_decimal(int(leg.american_odds)) for leg in won])
    american = decimal_to_american(decimal_odds)
    return "WON", profit_amount(stake, decimal_odds), decimal_odds, american
