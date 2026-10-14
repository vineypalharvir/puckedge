"""Bet365 / The Odds API client, with a synthetic board when no key is set."""

from __future__ import annotations

import logging
import math

from config import ODDS_API_BASE, ODDS_API_REGIONS, ODDS_BOOK_PRIORITY
from src.api.data_models import Game, GoalieProfile, Offer, PlayerProfile, SlateContext, TeamProfile
from src.api.http_client import get_json
from src.engine.devig import implied_to_american, poisson_sf_over
from src.engine.probs import expected_team_goals, final_score_probs

logger = logging.getLogger(__name__)

SYNTHETIC_BOOK = "synthetic-bet365"


def _clamp(value: float, low: float, high: float) -> float:
    return min(max(value, low), high)


def juiced_pair(fair_left: float, fair_right: float, hold: float = 0.045) -> tuple[int, int]:
    scale = 1.0 + hold
    left = _clamp(fair_left * scale, 0.04, 0.96)
    right = _clamp(fair_right * scale, 0.04, 0.96)
    return implied_to_american(left), implied_to_american(right)


def _norm_name(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


def _match_game(event: dict, games: list[Game]) -> Game | None:
    home = _norm_name(str(event.get("home_team") or ""))
    away = _norm_name(str(event.get("away_team") or ""))
    for game in games:
        if _norm_name(game.home.name) == home and _norm_name(game.away.name) == away:
            return game
    return None


def _offer(
    game: Game,
    *,
    market_type: str,
    market_key: str,
    target: str,
    selection: str,
    side: str,
    american: int,
    bookmaker: str,
    line: float | None = None,
    team_abbrev: str | None = None,
    opponent_abbrev: str | None = None,
    player_id: int | None = None,
    selectable: bool = True,
) -> Offer:
    return Offer(
        game_id=game.game_id,
        matchup=f"{game.away.abbrev} @ {game.home.abbrev}",
        game_date=game.game_date,
        start_time_utc=game.start_time_utc,
        market_type=market_type,
        market_key=market_key,
        target_entity=target,
        player_id=player_id,
        team_abbrev=team_abbrev,
        opponent_abbrev=opponent_abbrev,
        selection=selection,
        side=side,
        line=line,
        american_odds=int(american),
        bookmaker=bookmaker,
        selectable=selectable,
    )


def _event_books(bookmakers: list[dict]) -> list[dict]:
    seen: set[str] = set()
    books: list[dict] = []
    for book in bookmakers:
        key = str(book.get("key") or "")
        if not key or key in seen:
            continue
        seen.add(key)
        books.append(book)
    return books


def parse_odds_payload(payload: list | dict, games: list[Game]) -> list[Offer]:
    events = payload if isinstance(payload, list) else payload.get("data") or payload.get("events") or []
    offers: list[Offer] = []
    for event in events:
        game = _match_game(event, games)
        if game is None:
            continue
        for book in _event_books(event.get("bookmakers") or []):
            book_key = str(book.get("key") or "consensus")
            for market in book.get("markets") or []:
                key = market.get("key")
                outcomes = market.get("outcomes") or []
                if key == "h2h":
                    offers.extend(_moneyline_outcomes(game, outcomes, book_key, "MONEYLINE"))
                elif key in {"h2h_3_way", "h2h_3way"}:
                    offers.extend(_moneyline_outcomes(game, outcomes, book_key, "MONEYLINE_REG"))
                elif key == "spreads":
                    offers.extend(_spread_outcomes(game, outcomes, book_key))
                elif key == "totals":
                    offers.extend(_total_outcomes(game, outcomes, book_key))
    return offers


def _side_for_name(game: Game, name: str) -> tuple[str, str] | None:
    token = _norm_name(name)
    if token == _norm_name(game.home.name) or token == _norm_name(game.home.abbrev):
        return "HOME", game.home.abbrev
    if token == _norm_name(game.away.name) or token == _norm_name(game.away.abbrev):
        return "AWAY", game.away.abbrev
    if token in {"draw", "tie"}:
        return "DRAW", ""
    return None


def _moneyline_outcomes(game: Game, outcomes: list[dict], book: str, market_type: str) -> list[Offer]:
    offers = []
    market_key = f"{game.game_id}|{market_type}"
    for outcome in outcomes:
        mapped = _side_for_name(game, str(outcome.get("name") or ""))
        if mapped is None or outcome.get("price") is None:
            continue
        side, abbrev = mapped
        team = game.home if side == "HOME" else game.away if side == "AWAY" else None
        label = "Draw" if side == "DRAW" else (team.name if team else outcome["name"])
        opponent = game.away.abbrev if side == "HOME" else game.home.abbrev if side == "AWAY" else None
        offers.append(
            _offer(
                game,
                market_type=market_type,
                market_key=market_key,
                target=label,
                selection=f"{label} ML" if side != "DRAW" else "Regulation draw",
                side=side,
                american=int(outcome["price"]),
                bookmaker=book,
                team_abbrev=abbrev or None,
                opponent_abbrev=opponent,
                selectable=side != "DRAW",
            )
        )
    return offers


def _spread_outcomes(game: Game, outcomes: list[dict], book: str) -> list[Offer]:
    offers = []
    for outcome in outcomes:
        mapped = _side_for_name(game, str(outcome.get("name") or ""))
        point = outcome.get("point")
        if mapped is None or point is None or outcome.get("price") is None:
            continue
        side, abbrev = mapped
        if side not in {"HOME", "AWAY"}:
            continue
        team = game.home if side == "HOME" else game.away
        line = float(point)
        opponent = game.away.abbrev if side == "HOME" else game.home.abbrev
        offers.append(
            _offer(
                game,
                market_type="PUCKLINE",
                market_key=f"{game.game_id}|PUCKLINE|{abs(line):.1f}",
                target=team.name,
                selection=f"{team.name} {line:+.1f}",
                side=side,
                line=line,
                american=int(outcome["price"]),
                bookmaker=book,
                team_abbrev=abbrev,
                opponent_abbrev=opponent,
            )
        )
    return offers


def _total_outcomes(game: Game, outcomes: list[dict], book: str) -> list[Offer]:
    offers = []
    for outcome in outcomes:
        name = str(outcome.get("name") or "")
        point = outcome.get("point")
        if point is None or outcome.get("price") is None:
            continue
        side = "OVER" if name.casefold().startswith("over") else "UNDER" if name.casefold().startswith("under") else ""
        if not side:
            continue
        line = float(point)
        offers.append(
            _offer(
                game,
                market_type="TOTAL",
                market_key=f"{game.game_id}|TOTAL|{line:.1f}",
                target=f"{game.away.abbrev} @ {game.home.abbrev}",
                selection=f"{side.title()} {line:.1f}",
                side=side,
                line=line,
                american=int(outcome["price"]),
                bookmaker=book,
            )
        )
    return offers


def parse_prop_payload(payload: dict, game: Game, players: list[PlayerProfile]) -> list[Offer]:
    by_name = {_norm_name(player.name): player for player in players if player.team_abbrev in {game.home.abbrev, game.away.abbrev}}
    offers: list[Offer] = []
    for book in _event_books(payload.get("bookmakers") or []):
        book_key = str(book.get("key") or "consensus")
        for market in book.get("markets") or []:
            key = market.get("key")
            for outcome in market.get("outcomes") or []:
                description = str(outcome.get("description") or outcome.get("name") or "")
                player = by_name.get(_norm_name(description)) or by_name.get(_norm_name(str(outcome.get("name") or "")))
                if player is None or outcome.get("price") is None:
                    continue
                price = int(outcome["price"])
                point = outcome.get("point")
                opponent = game.away.abbrev if player.team_abbrev == game.home.abbrev else game.home.abbrev
                if key == "player_goal_scorer_anytime":
                    offers.append(
                        _offer(
                            game,
                            market_type="GOALSCORER",
                            market_key=f"{game.game_id}|GOALSCORER|{player.player_id}|0.5",
                            target=player.name,
                            selection=f"{player.name} anytime goal",
                            side="YES",
                            line=0.5,
                            american=price,
                            bookmaker=book_key,
                            team_abbrev=player.team_abbrev,
                            opponent_abbrev=opponent,
                            player_id=player.player_id,
                        )
                    )
                    continue
                if point is None:
                    continue
                side_name = str(outcome.get("name") or "")
                side = "OVER" if side_name.casefold().startswith("over") else "UNDER" if side_name.casefold().startswith("under") else "YES"
                line = float(point)
                if key == "player_shots_on_goal":
                    market_type = "SOG"
                    label = f"{player.name} {side.title()} {line:.1f} SOG"
                elif key == "player_points":
                    market_type = "POINTS"
                    label = f"{player.name} {side.title()} {line:.1f} points"
                else:
                    continue
                offers.append(
                    _offer(
                        game,
                        market_type=market_type,
                        market_key=f"{game.game_id}|{market_type}|{player.player_id}|{line:.1f}",
                        target=player.name,
                        selection=label,
                        side=side,
                        line=line,
                        american=price,
                        bookmaker=book_key,
                        team_abbrev=player.team_abbrev,
                        opponent_abbrev=opponent,
                        player_id=player.player_id,
                        selectable=side != "UNDER",
                    )
                )
    return offers


def available_books(offers: list[Offer]) -> list[str]:
    present = {offer.bookmaker for offer in offers if offer.bookmaker and offer.bookmaker != SYNTHETIC_BOOK}
    ordered = [book for book in ODDS_BOOK_PRIORITY if book in present]
    extras = sorted(present - set(ODDS_BOOK_PRIORITY))
    books = ordered + extras
    books.append(SYNTHETIC_BOOK)
    return books


def offers_for_book(offers: list[Offer], sportsbook: str) -> list[Offer]:
    if sportsbook == SYNTHETIC_BOOK:
        return []
    return [offer for offer in offers if offer.bookmaker == sportsbook]


def fetch_live_offers(
    games: list[Game],
    players: list[PlayerProfile],
    api_key: str,
    region: str | None = None,
) -> list[Offer]:
    if not api_key or not games:
        return []
    region = region or ODDS_API_REGIONS
    offers: list[Offer] = []
    try:
        payload = get_json(
            f"{ODDS_API_BASE}/sports/icehockey_nhl/odds",
            params={
                "apiKey": api_key,
                "regions": region,
                "markets": "h2h,spreads,totals",
                "oddsFormat": "american",
            },
            ttl_seconds=180,
        )
        offers.extend(parse_odds_payload(payload, games))
    except Exception as exc:
        logger.warning("Odds API game lines failed: %s", exc)
        return []

    try:
        events = get_json(
            f"{ODDS_API_BASE}/sports/icehockey_nhl/events",
            params={"apiKey": api_key},
            ttl_seconds=180,
        )
    except Exception as exc:
        logger.warning("Odds API events failed: %s", exc)
        return offers

    event_rows = events if isinstance(events, list) else []
    for event in event_rows:
        game = _match_game(event, games)
        if game is None or not event.get("id"):
            continue
        try:
            props = get_json(
                f"{ODDS_API_BASE}/sports/icehockey_nhl/events/{event['id']}/odds",
                params={
                    "apiKey": api_key,
                    "regions": region,
                    "markets": "player_points,player_shots_on_goal,player_goal_scorer_anytime",
                    "oddsFormat": "american",
                },
                ttl_seconds=180,
            )
        except Exception as exc:
            logger.warning("Player props failed for %s: %s", event.get("id"), exc)
            continue
        offers.extend(parse_prop_payload(props, game, players))
    return offers


def _book_goal_lambdas(home: TeamProfile, away: TeamProfile) -> tuple[float, float]:
    """Book prices season scoring only, shrunk toward league average, with no fatigue."""
    league = 3.0
    home_lam = expected_team_goals(home.gf_per_game, away.ga_per_game, league)
    away_lam = expected_team_goals(away.gf_per_game, home.ga_per_game, league)
    home_lam = 0.82 * home_lam + 0.18 * league
    away_lam = 0.82 * away_lam + 0.18 * league
    return home_lam, away_lam


def _two_way_props(fair_over: float, fair_under: float) -> tuple[int, int]:
    total = fair_over + fair_under
    if total <= 0:
        return -110, -110
    return juiced_pair(fair_over / total, fair_under / total)


def synthetic_offers(context: SlateContext) -> list[Offer]:
    players_by_team: dict[str, list[PlayerProfile]] = {}
    for player in context.players:
        players_by_team.setdefault(player.team_abbrev, []).append(player)
    goalies = {goalie.team_abbrev: goalie for goalie in context.goalies if goalie.projected_starter}
    if not goalies:
        for goalie in context.goalies:
            goalies.setdefault(goalie.team_abbrev, goalie)

    offers: list[Offer] = []
    for game in context.games:
        home = context.teams.get(game.home.abbrev)
        away = context.teams.get(game.away.abbrev)
        if home is None or away is None:
            continue
        offers.extend(_synthetic_game(game, home, away, players_by_team, goalies))
    return offers


def _synthetic_game(
    game: Game,
    home: TeamProfile,
    away: TeamProfile,
    players_by_team: dict[str, list[PlayerProfile]],
    goalies: dict[str, GoalieProfile],
) -> list[Offer]:
    offers: list[Offer] = []
    home_lam, away_lam = _book_goal_lambdas(home, away)
    matrix = final_score_probs(home_lam, away_lam)
    home_ml, away_ml = juiced_pair(matrix["p_home_ml"], matrix["p_away_ml"])
    # Small favorite shade so the model can disagree after form and fatigue.
    if home_ml < away_ml:
        home_ml -= 8
    elif away_ml < home_ml:
        away_ml -= 8
    offers.append(
        _offer(
            game,
            market_type="MONEYLINE",
            market_key=f"{game.game_id}|MONEYLINE",
            target=home.name,
            selection=f"{home.name} ML",
            side="HOME",
            american=home_ml,
            bookmaker=SYNTHETIC_BOOK,
            team_abbrev=home.abbrev,
            opponent_abbrev=away.abbrev,
        )
    )
    offers.append(
        _offer(
            game,
            market_type="MONEYLINE",
            market_key=f"{game.game_id}|MONEYLINE",
            target=away.name,
            selection=f"{away.name} ML",
            side="AWAY",
            american=away_ml,
            bookmaker=SYNTHETIC_BOOK,
            team_abbrev=away.abbrev,
            opponent_abbrev=home.abbrev,
        )
    )

    home_minus, away_plus = juiced_pair(matrix["p_home_cover_15"], matrix["p_away_plus_15"])
    away_minus, home_plus = juiced_pair(matrix["p_away_cover_15"], matrix["p_home_plus_15"])
    for team, side, opponent, line, price in (
        (home, "HOME", away.abbrev, -1.5, home_minus),
        (away, "AWAY", home.abbrev, 1.5, away_plus),
    ):
        offers.append(
            _offer(
                game,
                market_type="PUCKLINE",
                market_key=f"{game.game_id}|PUCKLINE|home-15",
                target=team.name,
                selection=f"{team.name} {line:+.1f}",
                side=side,
                line=line,
                american=price,
                bookmaker=SYNTHETIC_BOOK,
                team_abbrev=team.abbrev,
                opponent_abbrev=opponent,
            )
        )
    for team, side, opponent, line, price in (
        (away, "AWAY", home.abbrev, -1.5, away_minus),
        (home, "HOME", away.abbrev, 1.5, home_plus),
    ):
        offers.append(
            _offer(
                game,
                market_type="PUCKLINE",
                market_key=f"{game.game_id}|PUCKLINE|away-15",
                target=team.name,
                selection=f"{team.name} {line:+.1f}",
                side=side,
                line=line,
                american=price,
                bookmaker=SYNTHETIC_BOOK,
                team_abbrev=team.abbrev,
                opponent_abbrev=opponent,
            )
        )

    expected_total = home_lam + away_lam
    for line in (5.5, 6.0, 6.5):
        # Keep every total, but shade the alternate numbers so the main line is the one nearest expectation.
        distance = abs(expected_total - line)
        fair_over, _push = _hist_over(matrix["total"], line)
        fair_under = max(0.04, 1.0 - fair_over)
        over_price, under_price = _two_way_props(fair_over, fair_under)
        if distance > 0.45:
            over_price = int(over_price + math.copysign(12, line - expected_total))
            under_price = int(under_price - math.copysign(12, line - expected_total))
        for side, price in (("OVER", over_price), ("UNDER", under_price)):
            offers.append(
                _offer(
                    game,
                    market_type="TOTAL",
                    market_key=f"{game.game_id}|TOTAL|{line:.1f}",
                    target=f"{game.away.abbrev} @ {game.home.abbrev}",
                    selection=f"{side.title()} {line:.1f}",
                    side=side,
                    line=line,
                    american=price,
                    bookmaker=SYNTHETIC_BOOK,
                )
            )

    for team, opponent, hist in (
        (home, away, matrix["home_goals"]),
        (away, home, matrix["away_goals"]),
    ):
        for line in (1.5, 3.5):
            fair_over, _push = _hist_over(hist, line)
            fair_under = max(0.04, 1.0 - fair_over)
            over_price, under_price = _two_way_props(fair_over, fair_under)
            for side, price, selectable in (
                ("OVER", over_price, True),
                ("UNDER", under_price, True),
            ):
                offers.append(
                    _offer(
                        game,
                        market_type="TEAM_TOTAL",
                        market_key=f"{game.game_id}|TEAM_TOTAL|{team.abbrev}|{line:.1f}",
                        target=team.name,
                        selection=f"{team.name} {side.title()} {line:.1f}",
                        side=side,
                        line=line,
                        american=price,
                        bookmaker=SYNTHETIC_BOOK,
                        team_abbrev=team.abbrev,
                        opponent_abbrev=opponent.abbrev,
                selectable=selectable,
            )
        )

    for team in (home, away):
        opponent = away if team.abbrev == home.abbrev else home
        for player in players_by_team.get(team.abbrev, [])[:2]:
            offers.extend(_player_props(game, player, opponent))
        goalie = goalies.get(team.abbrev)
        if goalie is not None:
            offers.extend(_goalie_props(game, goalie, opponent))
    return offers


def _hist_over(hist: list[float], line: float) -> tuple[float, float]:
    win = 0.0
    push = 0.0
    mass = sum(hist) or 1.0
    for goals, prob in enumerate(hist):
        if goals > line + 1e-9:
            win += prob
        elif abs(goals - line) < 1e-9:
            push += prob
    return win / mass, push / mass


def _player_props(game: Game, player: PlayerProfile, opponent: TeamProfile) -> list[Offer]:
    offers: list[Offer] = []
    sog_lam = 0.85 * player.sog_per_game + 0.15 * 2.6
    for line in (2.5, 3.5):
        if line == 3.5 and player.sog_per_game < 2.4:
            continue
        over_p, push_p = poisson_sf_over(sog_lam, line)
        under_p = max(0.04, 1.0 - over_p - push_p)
        over_price, under_price = _two_way_props(over_p, under_p)
        for side, price, selectable in (("OVER", over_price, True), ("UNDER", under_price, False)):
            offers.append(
                _offer(
                    game,
                    market_type="SOG",
                    market_key=f"{game.game_id}|SOG|{player.player_id}|{line:.1f}",
                    target=player.name,
                    selection=f"{player.name} {side.title()} {line:.1f} SOG",
                    side=side,
                    line=line,
                    american=price,
                    bookmaker=SYNTHETIC_BOOK,
                    team_abbrev=player.team_abbrev,
                    opponent_abbrev=opponent.abbrev,
                    player_id=player.player_id,
                    selectable=selectable,
                )
            )

    points_lam = 0.85 * player.points_per_game + 0.15 * 0.7
    over_p, push_p = poisson_sf_over(points_lam, 0.5)
    under_p = max(0.04, 1.0 - over_p - push_p)
    over_price, under_price = _two_way_props(over_p, under_p)
    for side, price, selectable in (("OVER", over_price, True), ("UNDER", under_price, False)):
        offers.append(
            _offer(
                game,
                market_type="POINTS",
                market_key=f"{game.game_id}|POINTS|{player.player_id}|0.5",
                target=player.name,
                selection=f"{player.name} {side.title()} 0.5 points",
                side=side,
                line=0.5,
                american=price,
                bookmaker=SYNTHETIC_BOOK,
                team_abbrev=player.team_abbrev,
                opponent_abbrev=opponent.abbrev,
                player_id=player.player_id,
                selectable=selectable,
            )
        )

    goals_lam = 0.85 * max(player.goals_per_game, 0.08) + 0.15 * 0.28
    for line, label in ((0.5, "anytime goal"), (1.5, "2+ goals")):
        over_p, push_p = poisson_sf_over(goals_lam, line)
        under_p = max(0.04, 1.0 - over_p - push_p)
        yes_price, no_price = _two_way_props(over_p, under_p)
        offers.append(
            _offer(
                game,
                market_type="GOALSCORER",
                market_key=f"{game.game_id}|GOALSCORER|{player.player_id}|{line:.1f}",
                target=player.name,
                selection=f"{player.name} {label}",
                side="YES",
                line=line,
                american=yes_price,
                bookmaker=SYNTHETIC_BOOK,
                team_abbrev=player.team_abbrev,
                opponent_abbrev=opponent.abbrev,
                player_id=player.player_id,
            )
        )
        offers.append(
            _offer(
                game,
                market_type="GOALSCORER",
                market_key=f"{game.game_id}|GOALSCORER|{player.player_id}|{line:.1f}",
                target=player.name,
                selection=f"{player.name} no {label}",
                side="NO",
                line=line,
                american=no_price,
                bookmaker=SYNTHETIC_BOOK,
                team_abbrev=player.team_abbrev,
                opponent_abbrev=opponent.abbrev,
                player_id=player.player_id,
                selectable=False,
            )
        )
    return offers


def _goalie_props(game: Game, goalie: GoalieProfile, opponent: TeamProfile) -> list[Offer]:
    expected = _clamp(opponent.shots_for_per_game * goalie.save_pct, 18.0, 40.0)
    expected = 0.85 * expected + 0.15 * 26.5
    line = math.floor(expected) + 0.5
    over_p, push_p = poisson_sf_over(expected, line)
    under_p = max(0.04, 1.0 - over_p - push_p)
    over_price, under_price = _two_way_props(over_p, under_p)
    offers = []
    for side, price, selectable in (("OVER", over_price, True), ("UNDER", under_price, False)):
        offers.append(
            _offer(
                game,
                market_type="SAVES",
                market_key=f"{game.game_id}|SAVES|{goalie.player_id}|{line:.1f}",
                target=goalie.name,
                selection=f"{goalie.name} {side.title()} {line:.1f} saves",
                side=side,
                line=line,
                american=price,
                bookmaker=SYNTHETIC_BOOK,
                team_abbrev=goalie.team_abbrev,
                opponent_abbrev=opponent.abbrev,
                player_id=goalie.player_id,
                selectable=selectable,
            )
        )
    return offers


def merge_offers(live: list[Offer], synthetic: list[Offer]) -> tuple[list[Offer], str]:
    covered = {(offer.market_key, offer.side) for offer in live}
    # If a live book priced one side of a market, keep the synthetic opposite side only when
    # the live payload has no matching market key at all.
    live_keys = {offer.market_key for offer in live}
    combined = list(live)
    for offer in synthetic:
        if offer.market_key in live_keys:
            continue
        if (offer.market_key, offer.side) in covered:
            continue
        combined.append(offer)
    books = {offer.bookmaker for offer in combined}
    real_books = books - {SYNTHETIC_BOOK}
    if not real_books:
        source = SYNTHETIC_BOOK
    elif SYNTHETIC_BOOK in books:
        preferred = next((book for book in ODDS_BOOK_PRIORITY if book in real_books), "live")
        source = f"mixed:{preferred}"
    else:
        source = next((book for book in ODDS_BOOK_PRIORITY if book in real_books), "live")
    return combined, source
