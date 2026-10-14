"""Grade legs from a final box score and roll a ticket up."""

from __future__ import annotations

from src.api.data_models import BoxScore
from src.engine.devig import american_to_decimal, decimal_to_american


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
    return None


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


def grade_leg(leg, box: BoxScore | None) -> tuple[str, float | None, str]:
    if box is None or not box.is_final:
        state = box.game_state if box is not None else "missing"
        return "PENDING", None, f"Game is {state}."

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
            return "VOID", None, "Player was not in the final box score, so the leg is void."
        key = counted[market]
        if key == "sog":
            actual = float(skater.sog)
        elif key == "points":
            actual = float(skater.points)
        elif key == "goals":
            actual = float(skater.goals)
        elif key in skater.stats:
            actual = float(skater.stats[key])
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

    return "VOID", None, f"No grader for market {market}."


def rollup_ticket(legs, stake: float) -> tuple[str, float, float | None, int | None]:
    statuses = [leg.leg_status for leg in legs]
    if any(status == "LOST" for status in statuses):
        return "LOST", round(-stake, 2), None, None
    if any(status == "PENDING" for status in statuses):
        return "PENDING", 0.0, None, None
    won = [leg for leg in legs if leg.leg_status == "WON"]
    if not won:
        return "PUSHED", 0.0, None, None
    decimal_odds = 1.0
    for leg in won:
        decimal_odds *= american_to_decimal(int(leg.american_odds))
    american = decimal_to_american(decimal_odds)
    profit = round(stake * decimal_odds - stake, 2)
    return "WON", profit, decimal_odds, american
