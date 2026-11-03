"""Bet365 / The Odds API client, with a synthetic board when no key is set."""

from __future__ import annotations

import logging
import math

from config import ODDS_API_BASE, ODDS_API_KEY, ODDS_API_REGIONS, ODDS_BOOK_PRIORITY, SHARPAPI_KEY
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


def _shade_american(price: int, delta: float) -> int:
    """Move an American price by delta cents without landing in the invalid -99..+99 gap."""
    moved = int(price + delta)
    if -100 < moved < 100:
        return 100 if delta > 0 else -100
    return moved


def _stable_player_id(name: str) -> int:
    total = 0
    for char in _norm_name(name):
        total = (total * 33 + ord(char)) % 10_000_000
    return total or 1


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


def parse_odds_payload(payload: list | dict, games: list[Game], spread_type: str = "PUCKLINE") -> list[Offer]:
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
                    offers.extend(_spread_outcomes(game, outcomes, book_key, spread_type))
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


def _spread_outcomes(game: Game, outcomes: list[dict], book: str, market_type: str = "PUCKLINE") -> list[Offer]:
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
                market_type=market_type,
                market_key=f"{game.game_id}|{market_type}|{abs(line):.1f}",
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


PROP_KEY_MAP = {
    "player_anytime_td": "ANYTIME_TD",
    "player_pass_yds": "PASS_YDS",
    "player_rush_yds": "RUSH_YDS",
    "player_reception_yds": "REC_YDS",
    "player_points": "POINTS",
    "player_rebounds": "REBOUNDS",
    "player_assists": "ASSISTS",
    "player_threes": "THREES",
    "player_shots_on_goal": "SOG",
    "player_goal_scorer_anytime": "GOALSCORER",
}


def parse_prop_payload(payload: dict, game: Game, players: list[PlayerProfile]) -> list[Offer]:
    by_name = {_norm_name(player.name): player for player in players if player.team_abbrev in {game.home.abbrev, game.away.abbrev}}
    offers: list[Offer] = []
    for book in _event_books(payload.get("bookmakers") or []):
        book_key = str(book.get("key") or "consensus")
        for market in book.get("markets") or []:
            key = market.get("key")
            market_type = PROP_KEY_MAP.get(key)
            if market_type is None:
                continue
            for outcome in market.get("outcomes") or []:
                description = str(outcome.get("description") or outcome.get("name") or "")
                named = description if _norm_name(description) not in {"", "over", "under", "yes", "no"} else str(outcome.get("name") or "")
                player = by_name.get(_norm_name(description)) or by_name.get(_norm_name(named))
                if player is None and _norm_name(named) not in {"", "over", "under", "yes", "no"}:
                    player = PlayerProfile(
                        player_id=_stable_player_id(named),
                        name=named,
                        team_abbrev="",
                        position="",
                    )
                if player is None or outcome.get("price") is None:
                    continue
                price = int(outcome["price"])
                opponent = game.away.abbrev if player.team_abbrev == game.home.abbrev else game.home.abbrev
                if market_type in {"GOALSCORER", "ANYTIME_TD"}:
                    label = "anytime goal" if market_type == "GOALSCORER" else "anytime TD"
                    offers.append(
                        _offer(
                            game,
                            market_type=market_type,
                            market_key=f"{game.game_id}|{market_type}|{player.player_id}|0.5",
                            target=player.name,
                            selection=f"{player.name} {label}",
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
                if outcome.get("point") is None:
                    continue
                line = float(outcome["point"])
                side_name = str(outcome.get("name") or "")
                side = "OVER" if side_name.casefold().startswith("over") else "UNDER" if side_name.casefold().startswith("under") else "YES"
                unit = " SOG" if market_type == "SOG" else " points" if market_type == "POINTS" else ""
                offers.append(
                    _offer(
                        game,
                        market_type=market_type,
                        market_key=f"{game.game_id}|{market_type}|{player.player_id}|{line:.1f}",
                        target=player.name,
                        selection=f"{player.name} {side.title()} {line:.1f}{unit}",
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


PLAYER_PROP_MARKETS = {
    "ANYTIME_TD",
    "GOALSCORER",
    "PASS_YDS",
    "RUSH_YDS",
    "REC_YDS",
    "POINTS",
    "REBOUNDS",
    "ASSISTS",
    "THREES",
    "SOG",
    "SAVES",
}


def _prop_identity(offer: Offer) -> tuple:
    line = None if offer.line is None else round(float(offer.line), 1)
    return (offer.game_id, offer.market_type, _norm_name(offer.target_entity), offer.side, line)


def pick_prop_offers(offers: list[Offer], preferred: str) -> list[Offer]:
    """One player prop per market. Prefer the selected book, then any other book that posted it."""
    groups: dict[tuple, list[Offer]] = {}
    for offer in offers:
        if offer.market_type not in PLAYER_PROP_MARKETS or not offer.selectable:
            continue
        if offer.bookmaker == SYNTHETIC_BOOK:
            continue
        groups.setdefault(_prop_identity(offer), []).append(offer)
    picked: list[Offer] = []
    for group in groups.values():
        chosen = next((offer for offer in group if offer.bookmaker == preferred), None)
        if chosen is None:
            for book in ODDS_BOOK_PRIORITY:
                chosen = next((offer for offer in group if offer.bookmaker == book), None)
                if chosen is not None:
                    break
        picked.append(chosen or group[0])
    return picked


def offers_for_book(offers: list[Offer], sportsbook: str) -> list[Offer]:
    if sportsbook == SYNTHETIC_BOOK:
        return []
    return [offer for offer in offers if offer.bookmaker == sportsbook]


SPORT_KEYS = {
    "NHL": "icehockey_nhl",
    "NFL": "americanfootball_nfl",
    "NBA": "basketball_nba",
}
PROP_MARKETS = {
    "NHL": "player_points,player_shots_on_goal,player_goal_scorer_anytime",
    "NFL": "player_pass_yds,player_rush_yds,player_reception_yds,player_anytime_td",
    "NBA": "player_points,player_rebounds,player_assists,player_threes",
}
GAME_MARKETS = "h2h,spreads,totals"
# One page load asks for NHL, NFL, and NBA. Split the remaining credits across them.
_credits_remaining: int | None = None
_sport_slots = 3


def _csv_parts(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def fit_odds_query(regions: str, markets: str, remaining: int) -> tuple[str, str] | None:
    """Largest region/market pair whose product fits the credits still on the key.

    The Odds API charges regions × markets. More markets are kept before extra regions.
    """
    region_list = _csv_parts(regions)
    market_list = _csv_parts(markets)
    if remaining <= 0 or not region_list or not market_list:
        return None
    for market_count in range(len(market_list), 0, -1):
        for region_count in range(len(region_list), 0, -1):
            if region_count * market_count <= remaining:
                return ",".join(region_list[:region_count]), ",".join(market_list[:market_count])
    return None


def _credit_share(remaining: int) -> int:
    global _sport_slots
    slots = max(_sport_slots, 1)
    share = remaining if slots == 1 else max(1, remaining // slots)
    _sport_slots = max(slots - 1, 1)
    return min(share, remaining)


def _remember_credits(headers) -> None:
    global _credits_remaining
    if not headers:
        return
    raw = headers.get("x-requests-remaining")
    if raw is None:
        return
    try:
        _credits_remaining = int(raw)
    except (TypeError, ValueError):
        return


def _quota_error(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if response is None or getattr(response, "status_code", None) != 401:
        return False
    try:
        body = response.json()
    except Exception:
        body = {}
    code = str(body.get("error_code") or "")
    message = str(body.get("message") or "")
    return code == "OUT_OF_USAGE_CREDITS" or "quota" in message.lower()


def _safe_http_error(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if response is None:
        return type(exc).__name__
    try:
        body = response.json()
        detail = str(body.get("error_code") or body.get("message") or "")
    except Exception:
        detail = ""
    return f"HTTP {getattr(response, 'status_code', '')} {detail}".strip()


def _odds_get(url: str, params: dict) -> tuple[object, dict]:
    sink: dict = {}
    body = get_json(url, params=params, ttl_seconds=180, header_sink=sink)
    _remember_credits(sink)
    return body, sink


def fetch_live_offers(
    games: list[Game],
    players: list[PlayerProfile],
    api_key: str,
    region: str | None = None,
    sport: str = "NHL",
) -> list[Offer]:
    fetch_live_offers.last_note = ""
    if not games:
        return []
    sharp_key = (api_key or SHARPAPI_KEY).strip()
    if sharp_key:
        from src.api.sharp_client import fetch_sharp_offers

        try:
            offers = fetch_sharp_offers(games, players, sharp_key, sport)
        except Exception as exc:
            logger.warning("SharpAPI lines failed: %s", _safe_http_error(exc))
            fetch_live_offers.last_note = "SharpAPI did not return lines. Synthetic prices are filling the board."
            return []
        if not offers:
            fetch_live_offers.last_note = "SharpAPI returned no lines for these games. Synthetic prices are filling the board."
        elif getattr(fetch_sharp_offers, "last_note", ""):
            fetch_live_offers.last_note = fetch_sharp_offers.last_note
        return offers
    if not api_key:
        api_key = ODDS_API_KEY
    if not api_key:
        return []
    region = region or ODDS_API_REGIONS
    sport_key = SPORT_KEYS.get(sport, SPORT_KEYS["NHL"])
    spread_type = "PUCKLINE" if sport == "NHL" else "SPREAD"
    offers: list[Offer] = []
    regions, markets = region, GAME_MARKETS
    if _credits_remaining is not None:
        fitted = fit_odds_query(region, GAME_MARKETS, _credit_share(_credits_remaining))
        if fitted is None:
            fetch_live_offers.last_note = (
                f"Odds API has {_credits_remaining} credits left, which is not enough for {sport} lines. "
                "The board is using synthetic prices until the quota resets."
            )
            logger.warning(fetch_live_offers.last_note)
            return []
        regions, markets = fitted
    try:
        payload, _sink = _odds_get(
            f"{ODDS_API_BASE}/sports/{sport_key}/odds",
            {"apiKey": api_key, "regions": regions, "markets": markets, "oddsFormat": "american"},
        )
    except Exception as exc:
        if not _quota_error(exc):
            logger.warning("Odds API game lines failed: %s", _safe_http_error(exc))
            return []
        response = getattr(exc, "response", None)
        _remember_credits(getattr(response, "headers", None))
        remaining = _credits_remaining or 0
        fitted = fit_odds_query(region, GAME_MARKETS, _credit_share(remaining))
        if fitted is None or fitted == (regions, markets):
            fetch_live_offers.last_note = (
                f"Odds API usage credits are used up ({remaining} left). "
                "A full US/UK/EU request needs more than that, so this board is on synthetic prices."
            )
            logger.warning(fetch_live_offers.last_note)
            return []
        regions, markets = fitted
        try:
            payload, _sink = _odds_get(
                f"{ODDS_API_BASE}/sports/{sport_key}/odds",
                {"apiKey": api_key, "regions": regions, "markets": markets, "oddsFormat": "american"},
            )
        except Exception as retry_exc:
            logger.warning("Odds API game lines failed: %s", _safe_http_error(retry_exc))
            return []
    if (regions, markets) != (region, GAME_MARKETS) and not fetch_live_offers.last_note:
        left = _credits_remaining if _credits_remaining is not None else "unknown"
        fetch_live_offers.last_note = (
            f"Odds API credits are low ({left} left), so {sport} used regions {regions} and markets {markets}."
        )
    if fetch_live_offers.last_note:
        logger.warning(fetch_live_offers.last_note)
    if not isinstance(payload, list):
        return offers
    offers.extend(parse_odds_payload(payload, games, spread_type))

    try:
        events, _sink = _odds_get(
            f"{ODDS_API_BASE}/sports/{sport_key}/events",
            {"apiKey": api_key},
        )
    except Exception as exc:
        logger.warning("Odds API events failed: %s", _safe_http_error(exc))
        return offers

    event_rows = events if isinstance(events, list) else []
    prop_markets = PROP_MARKETS.get(sport, PROP_MARKETS["NHL"])
    prop_cost = len(_csv_parts(regions)) * len(_csv_parts(prop_markets))
    if (regions, markets) != (region, GAME_MARKETS):
        logger.warning("Skipping %s player props so the remaining credits can cover the other leagues.", sport)
        event_rows = []
    for event in event_rows:
        game = _match_game(event, games)
        if game is None or not event.get("id"):
            continue
        if _credits_remaining is not None and _credits_remaining < prop_cost:
            logger.warning(
                "Skipping %s player props; %s credits left and one prop call needs %s.",
                sport,
                _credits_remaining,
                prop_cost,
            )
            break
        try:
            props, _sink = _odds_get(
                f"{ODDS_API_BASE}/sports/{sport_key}/events/{event['id']}/odds",
                {
                    "apiKey": api_key,
                    "regions": regions,
                    "markets": prop_markets,
                    "oddsFormat": "american",
                },
            )
        except Exception as exc:
            logger.warning("Player props failed for %s: %s", event.get("id"), _safe_http_error(exc))
            if _quota_error(exc):
                break
            continue
        if isinstance(props, dict):
            offers.extend(parse_prop_payload(props, game, players))
    return offers


fetch_live_offers.last_note = ""


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
            over_price = _shade_american(over_price, math.copysign(12, line - expected_total))
            under_price = _shade_american(under_price, -math.copysign(12, line - expected_total))
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
    # A book's main total or spread replaces the synthetic one even when the lines differ.
    game_level = {"MONEYLINE", "SPREAD", "PUCKLINE", "TOTAL"}
    live_games = {(offer.game_id, offer.market_type) for offer in live if offer.market_type in game_level}
    live_props = {
        (offer.game_id, offer.market_type, _norm_name(offer.target_entity))
        for offer in live
        if offer.market_type not in game_level
    }
    combined = list(live)
    for offer in synthetic:
        if offer.market_key in live_keys:
            continue
        if offer.market_type in game_level and (offer.game_id, offer.market_type) in live_games:
            continue
        if (offer.game_id, offer.market_type, _norm_name(offer.target_entity)) in live_props:
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


def dedupe_offers(offers: list[Offer]) -> list[Offer]:
    """Keep one offer per book, market, and side so a repeated row cannot count twice in a devig."""
    seen: set[tuple[str, str, str]] = set()
    unique: list[Offer] = []
    for offer in offers:
        key = (offer.bookmaker, offer.market_key, offer.side)
        if key in seen:
            continue
        seen.add(key)
        unique.append(offer)
    return unique
