"""Same-game synergy and hard conflicts."""

from __future__ import annotations

from src.api.data_models import MarketLeg


def _same_game(left: MarketLeg, right: MarketLeg) -> bool:
    return left.game_id == right.game_id


MONEYLINES = {"MONEYLINE", "MONEYLINE_REG"}
SPREADS = {"PUCKLINE", "SPREAD"}
SCORER_MARKETS = {"GOALSCORER", "ANYTIME_TD"}
# Most points the ranking can add for stacked same-game synergies. Without a cap a
# five-leg stack could out-rank a clearly better priced ticket on correlation alone.
SYNERGY_CAP = 6


def _relation(left: MarketLeg, right: MarketLeg) -> str | None:
    """'same' or 'opposite' when two legs sit on the same or the other team of a game."""
    if left.team_abbrev and right.team_abbrev:
        return "same" if left.team_abbrev == right.team_abbrev else "opposite"
    if left.side in {"HOME", "AWAY"} and right.side in {"HOME", "AWAY"}:
        return "same" if left.side == right.side else "opposite"
    return None


def pair_conflicts(left: MarketLeg, right: MarketLeg) -> bool:
    """True when two legs of one game cannot both be a sensible part of the same ticket.

    Blocked pairs: both sides of one market (Over and Under, home and away); any two
    totals, any two spreads (so -1.5 on one team with +1.5 on the other, and alternate
    lines, are out); two totals for the same team; moneylines for opposite teams; a
    moneyline with any spread, whether it is that team's own spread or the opponent's.
    """
    if not _same_game(left, right):
        return False
    if left.market_key == right.market_key and left.side != right.side:
        return True
    if left.market_type == right.market_type and left.market_type in {"TOTAL", "SPREAD", "PUCKLINE"}:
        return True
    if (
        left.market_type == "TEAM_TOTAL"
        and right.market_type == "TEAM_TOTAL"
        and _relation(left, right) == "same"
    ):
        return True
    if (
        left.market_type == right.market_type
        and left.market_type not in PLAYER_BOARD_MARKETS_EXCLUDED
        and left.target_entity
        and left.target_entity == right.target_entity
    ):
        # The same player on the same stat twice (Over 2.5 and Over 3.5, or Over and Under).
        return True
    if left.market_type in MONEYLINES and right.market_type in MONEYLINES:
        if left.market_type == right.market_type and left.side != right.side:
            return True
        if _relation(left, right) == "opposite":
            return True
    moneyline_and_spread = (left.market_type in MONEYLINES and right.market_type in SPREADS) or (
        left.market_type in SPREADS and right.market_type in MONEYLINES
    )
    if moneyline_and_spread and _relation(left, right) in {"same", "opposite"}:
        return True
    return False


def ticket_conflicts(legs: list[MarketLeg]) -> bool:
    """Pairwise conflicts plus the Under-with-scorers rule.

    A game Under stacked with two or more anytime scorers (anytime goal in the NHL,
    anytime TD in the NFL, any total line) is blocked: every scorer needs the game
    to produce points while the Under needs it not to. One scorer with an Under is
    allowed because a single goal or touchdown fits comfortably inside an Under.
    """
    for index, left in enumerate(legs):
        for right in legs[index + 1 :]:
            if pair_conflicts(left, right):
                return True
    by_game: dict[str, list[MarketLeg]] = {}
    for leg in legs:
        by_game.setdefault(leg.game_id, []).append(leg)
    for group in by_game.values():
        unders = [leg for leg in group if leg.market_type == "TOTAL" and leg.side == "UNDER"]
        scorers = [leg for leg in group if leg.market_type in SCORER_MARKETS and leg.side == "YES"]
        if unders and len(scorers) >= 2:
            return True
    return False


PLAYER_BOARD_MARKETS_EXCLUDED = MONEYLINES | SPREADS | {"TOTAL", "TEAM_TOTAL", "CUSTOM", "PROP"}


def _slip_leg(quote: dict) -> MarketLeg | None:
    """Wrap a slip quote as a MarketLeg so the same conflict rules apply to hand-built slips.

    The prices on the wrapper are placeholders. Conflict rules never read them.
    Quotes without a market type or a game key cannot be compared and are skipped.
    """
    market_type = str(quote.get("market_type") or "").upper()
    game_key = str(quote.get("game_key") or "")
    if not market_type or not game_key:
        return None
    side = str(quote.get("pick") or "").upper()
    line = quote.get("line")
    team = str(quote.get("team") or "") or None
    player = str(quote.get("player") or "")
    key = str(quote.get("market_key") or f"{game_key}|{market_type}|{team or player}|{line}")
    return MarketLeg(
        leg_id=str(quote.get("id") or key),
        game_id=game_key,
        matchup=str(quote.get("matchup") or ""),
        game_date="",
        start_time_utc="",
        market_type=market_type,
        market_key=key,
        target_entity=player,
        team_abbrev=team,
        selection=str(quote.get("selection") or ""),
        side=side,
        line=None if line is None else float(line),
        american_odds=100,
        decimal_odds=2.0,
        implied_prob=0.5,
        fair_prob=0.5,
        model_prob=0.5,
        ev=0.0,
        bookmaker=str(quote.get("book") or ""),
        rationale="",
    )


def slip_conflict(new: dict, existing: list[dict]) -> str | None:
    """A plain-language reason the new slip leg cannot join the slip, or None when it fits."""
    candidate = _slip_leg(new)
    if candidate is None:
        return None
    others = [leg for leg in (_slip_leg(item) for item in existing) if leg is not None]
    for other in others:
        if pair_conflicts(other, candidate):
            return f"{candidate.selection} fights with {other.selection} in the same game, so it was not added."
    if ticket_conflicts([*others, candidate]):
        return f"{candidate.selection} stacks a game Under with several anytime scorers, so it was not added."
    return None

def synergy(legs: list[MarketLeg]) -> tuple[int, list[str]]:
    """Ranking bonus for positively correlated same-game legs, with the notes that explain it.

    The bonus only reorders candidate tickets. It never changes the displayed hit
    chance, which stays the product of the leg chances, so a correlation is counted
    once (in rank) and cannot push a probability above 100%. Each distinct pairing
    scores once and the total is capped at SYNERGY_CAP.
    """
    score = 0
    notes: list[str] = []

    def add(points: int, note: str) -> None:
        nonlocal score
        if note in notes:
            return
        notes.append(note)
        score += points

    by_game: dict[str, list[MarketLeg]] = {}
    for leg in legs:
        by_game.setdefault(leg.game_id, []).append(leg)

    for group in by_game.values():
        minus = [
            leg
            for leg in group
            if leg.market_type in {"PUCKLINE", "SPREAD"} and leg.line is not None and leg.line < 0
        ]
        team_overs = [
            leg
            for leg in group
            if leg.market_type == "TEAM_TOTAL" and leg.side == "OVER" and leg.line is not None
        ]
        for cover in minus:
            for total in team_overs:
                if not cover.team_abbrev or cover.team_abbrev != total.team_abbrev:
                    continue
                if cover.market_type == "PUCKLINE" and (total.line or 0) < 3.5:
                    continue
                add(2, f"Synergy: {cover.target_entity} {cover.line:+.1f} pairs with {total.selection}.")
        points = [leg for leg in group if leg.market_type == "POINTS" and leg.side == "OVER"]
        shots = [leg for leg in group if leg.market_type == "SOG" and leg.side == "OVER"]
        for point_leg in points:
            for shot_leg in shots:
                if point_leg.player_id == shot_leg.player_id:
                    continue
                if point_leg.team_abbrev and point_leg.team_abbrev == shot_leg.team_abbrev:
                    add(
                        2,
                        f"Synergy: {point_leg.target_entity} points with {shot_leg.target_entity} shots, a shared top-unit stack.",
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
                add(2, f"Synergy: {save_leg.target_entity} save total with {opponents[0].target_entity} shot volume.")
        passers = [leg for leg in group if leg.market_type == "PASS_YDS" and leg.side == "OVER"]
        receivers = [leg for leg in group if leg.market_type == "REC_YDS" and leg.side == "OVER"]
        for passer in passers:
            for receiver in receivers:
                if passer.player_id and passer.player_id == receiver.player_id:
                    continue
                if passer.team_abbrev and passer.team_abbrev == receiver.team_abbrev:
                    add(
                        2,
                        f"Synergy: {passer.target_entity} passing yards with {receiver.target_entity} receiving yards, the same offense.",
                    )
                    break
    return min(score, SYNERGY_CAP), notes