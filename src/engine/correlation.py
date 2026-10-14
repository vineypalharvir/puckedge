"""Same-game synergy and hard conflicts."""

from __future__ import annotations

from src.api.data_models import MarketLeg


def _same_game(left: MarketLeg, right: MarketLeg) -> bool:
    return left.game_id == right.game_id


def pair_conflicts(left: MarketLeg, right: MarketLeg) -> bool:
    if not _same_game(left, right):
        return False
    if left.market_key == right.market_key and left.side != right.side:
        return True
    moneylines = {"MONEYLINE", "MONEYLINE_REG"}
    if left.market_type in moneylines and right.market_type in moneylines:
        if left.market_type == right.market_type and left.side != right.side:
            return True
        if {left.side, right.side} <= {"HOME", "AWAY"} and left.team_abbrev != right.team_abbrev:
            return True
    if left.market_type == "PUCKLINE" and right.market_type == "MONEYLINE":
        if left.line is not None and left.line < 0 and left.team_abbrev != right.team_abbrev and right.side in {"HOME", "AWAY"}:
            return True
    if right.market_type == "PUCKLINE" and left.market_type == "MONEYLINE":
        return pair_conflicts(right, left)
    if left.team_abbrev and left.team_abbrev == right.team_abbrev and {left.market_type, right.market_type} == {"MONEYLINE", "PUCKLINE"}:
        return True
    return False


def ticket_conflicts(legs: list[MarketLeg]) -> bool:
    for index, left in enumerate(legs):
        for right in legs[index + 1 :]:
            if pair_conflicts(left, right):
                return True
    by_game: dict[str, list[MarketLeg]] = {}
    for leg in legs:
        by_game.setdefault(leg.game_id, []).append(leg)
    for group in by_game.values():
        unders = [
            leg
            for leg in group
            if leg.market_type == "TOTAL" and leg.side == "UNDER" and leg.line is not None and leg.line <= 5.5
        ]
        scorers = [leg for leg in group if leg.market_type == "GOALSCORER" and leg.side == "YES"]
        if unders and len(scorers) >= 2:
            return True
    return False


def synergy(legs: list[MarketLeg]) -> tuple[int, list[str]]:
    score = 0
    notes: list[str] = []
    by_game: dict[str, list[MarketLeg]] = {}
    for leg in legs:
        by_game.setdefault(leg.game_id, []).append(leg)

    for group in by_game.values():
        minus = [
            leg
            for leg in group
            if leg.market_type == "PUCKLINE" and leg.line is not None and leg.line < 0
        ]
        team_overs = [
            leg
            for leg in group
            if leg.market_type == "TEAM_TOTAL" and leg.side == "OVER" and leg.line is not None and leg.line >= 3.5
        ]
        for cover in minus:
            for total in team_overs:
                if cover.team_abbrev and cover.team_abbrev == total.team_abbrev:
                    score += 2
                    notes.append(
                        f"Synergy: {cover.target_entity} {cover.line:+.1f} pairs with {total.selection}."
                    )
        points = [leg for leg in group if leg.market_type == "POINTS" and leg.side == "OVER"]
        shots = [leg for leg in group if leg.market_type == "SOG" and leg.side == "OVER"]
        for point_leg in points:
            for shot_leg in shots:
                if point_leg.player_id == shot_leg.player_id:
                    continue
                if point_leg.team_abbrev and point_leg.team_abbrev == shot_leg.team_abbrev:
                    score += 2
                    notes.append(
                        f"Synergy: {point_leg.target_entity} points with {shot_leg.target_entity} shots, a shared top-unit stack."
                    )
                    break
        saves = [leg for leg in group if leg.market_type == "SAVES" and leg.side == "OVER"]
        for save_leg in saves:
            opponents = [
                leg
                for leg in shots
                if leg.team_abbrev and leg.team_abbrev == save_leg.opponent_abbrev
            ]
            if opponents:
                score += 2
                notes.append(
                    f"Synergy: {save_leg.target_entity} save total with {opponents[0].target_entity} shot volume."
                )
    # De-duplicate notes while keeping order.
    unique: list[str] = []
    for note in notes:
        if note not in unique:
            unique.append(note)
    return score, unique
