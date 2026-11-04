"""SharpAPI odds snapshot, mapped onto the games already on the board."""

from __future__ import annotations

import logging
import time

import httpx

from config import SHARPAPI_BASE
from src.api.data_models import Game, Offer, PlayerProfile, TeamSide
from src.api.http_client import get_json
from src.engine.devig import is_valid_american
from src.api.odds_client import _norm_name, _offer, _stable_player_id, dedupe_offers

logger = logging.getLogger(__name__)

PAGE_LIMIT = 200  # the API's maximum
MAX_PAGES = 60  # safety stop across one board load
EVENTS_PER_BATCH = 3
# The free tier gives about 12 requests a minute and a board needs 16 to 30, so props never wait
# for the quota. They stop with calls to spare, and every page is saved for PROPS_TTL, so each
# reload picks up where the last one stopped instead of blocking the page.
PROPS_BUDGET_SECONDS = 20.0
QUOTA_RESERVE = 4  # calls left for the other leagues' main lines
PROPS_TTL = 900
MAX_WAIT_SECONDS = 65.0  # the free tier window is one minute
_quota: dict = {"remaining": None, "at": 0.0}
MAX_THROTTLE_WAITS = 3
_sleep = time.sleep
_clock = time.monotonic
_now = time.time

LEAGUE = {"NHL": "nhl", "NBA": "nba", "NFL": "nfl"}
MAIN_MARKETS = {
    "NHL": "moneyline,puck_line,total_goals",
    "NBA": "moneyline,point_spread,total_points",
    "NFL": "moneyline,point_spread,total_points",
}
SPREAD_TYPE = {"NHL": "PUCKLINE", "NBA": "SPREAD", "NFL": "SPREAD"}
PROP_MARKETS = {
    "player_points": "POINTS",
    "player_rebounds": "REBOUNDS",
    "player_assists": "ASSISTS",
    "player_threes": "THREES",
    "player_shots_on_goal": "SOG",
    "player_goal_scorer_anytime": "GOALSCORER",
    "player_anytime_goal": "GOALSCORER",
    "player_pass_yds": "PASS_YDS",
    "player_rush_yds": "RUSH_YDS",
    "player_reception_yds": "REC_YDS",
    "player_anytime_td": "ANYTIME_TD",
}
# Markets SharpAPI really returns (seen in saved responses) that the Studio board does not use.
# Only the OmniScreener asks for them (extended=True), so Studio parlays never pick up a leg
# the ledger cannot grade. "player_goals" carries both anytime (line 0.5) and 2+ goals (line 1.5).
EXTRA_PROP_MARKETS = {
    "player_goals": "GOALS",
    "player_made_threes": "THREES",
    "player_blocked_shots": "BLOCKS",
    "player_saves": "SAVES",
    "player_rushing_yards": "RUSH_YDS",
    "player_receiving_yards": "REC_YDS",
    "player_passing_yards": "PASS_YDS",
    "player_passing_touchdowns": "PASS_TD",
    "player_rushing_attempts": "RUSH_ATT",
    "player_receptions": "RECEPTIONS",
    "player_points_+_rebounds_+_assists": "PRA",
    "player_steals_+_blocks": "STOCKS",
    "player_steals": "STEALS",
    "player_blocks": "BLOCKS",
    "player_double_double": "DOUBLE_DOUBLE",
}
TOTAL_TYPES = {"total_points", "total_goals", "total_runs"}
SPREAD_TYPES = {"point_spread", "puck_line", "run_line"}
YES_TYPES = {"GOALSCORER", "ANYTIME_TD"}
YES_LABELS = {"GOALSCORER": "anytime goal", "ANYTIME_TD": "anytime TD", "DOUBLE_DOUBLE": "double-double"}


def _same_team(label: str, side: TeamSide) -> bool:
    token = _norm_name(label)
    full = _norm_name(side.name)
    abbrev = _norm_name(side.abbrev)
    if not token or not full:
        return False
    if token == full or token == abbrev:
        return True
    parts = label.split()
    if len(parts) < 2:
        return token in full
    city = _norm_name(" ".join(parts[:-1]))
    nick = _norm_name(parts[-1])
    if not nick or nick not in full:
        return False
    return city in full or city == abbrev or abbrev.startswith(city) or city.startswith(abbrev)


def _match_game(row: dict, games: list[Game]) -> Game | None:
    home = str(row.get("home_team") or "")
    away = str(row.get("away_team") or "")
    for game in games:
        if _same_team(home, game.home) and _same_team(away, game.away):
            return game
    return None


def _side(row: dict, game: Game, extended: bool = False) -> tuple[str, str] | None:
    kind = str(row.get("selection_type") or "").casefold()
    if extended and kind == "other" and str(row.get("selection") or "").casefold() in {"yes", "no"}:
        kind = str(row.get("selection")).casefold()
    if kind == "home":
        return "HOME", game.home.abbrev
    if kind == "away":
        return "AWAY", game.away.abbrev
    if kind == "over":
        return "OVER", ""
    if kind == "under":
        return "UNDER", ""
    if kind in {"yes", "no"}:
        return kind.upper(), ""
    return None


def parse_sharp_rows(
    rows: list[dict], games: list[Game], players: list[PlayerProfile], sport: str, extended: bool = False
) -> list[Offer]:
    by_name = {_norm_name(player.name): player for player in players}
    offers: list[Offer] = []
    spread_name = SPREAD_TYPE.get(sport, "SPREAD")
    for row in rows:
        if row.get("is_main_line") is False:
            continue
        # The feed marks prices a book has pulled, left stale, or is only showing in-play.
        if row.get("is_active") is False or row.get("is_stale_pregame_price") is True:
            continue
        if row.get("is_live") is True or row.get("is_impossible_scoreline") is True:
            continue
        if row.get("odds_american") is None:
            continue
        game = _match_game(row, games)
        if game is None:
            continue
        market = str(row.get("market_type") or "")
        book = str(row.get("sportsbook") or "sharpapi")
        side = _side(row, game, extended)
        if side is None:
            continue
        side_name, abbrev = side
        try:
            american = int(row["odds_american"])
        except (TypeError, ValueError):
            continue
        if not is_valid_american(american):
            continue
        line = row.get("line")
        number = float(line) if line is not None else None
        if market == "moneyline" and side_name in {"HOME", "AWAY"}:
            team = game.home if side_name == "HOME" else game.away
            opponent = game.away.abbrev if side_name == "HOME" else game.home.abbrev
            offers.append(
                _offer(
                    game,
                    market_type="MONEYLINE",
                    market_key=f"{game.game_id}|MONEYLINE",
                    target=team.name,
                    selection=f"{team.name} ML",
                    side=side_name,
                    american=american,
                    bookmaker=book,
                    team_abbrev=abbrev,
                    opponent_abbrev=opponent,
                )
            )
            continue
        if market in SPREAD_TYPES and side_name in {"HOME", "AWAY"} and number is not None:
            team = game.home if side_name == "HOME" else game.away
            opponent = game.away.abbrev if side_name == "HOME" else game.home.abbrev
            offers.append(
                _offer(
                    game,
                    market_type=spread_name,
                    market_key=f"{game.game_id}|{spread_name}|{abs(number):.1f}",
                    target=team.name,
                    selection=f"{team.name} {number:+.1f}",
                    side=side_name,
                    line=number,
                    american=american,
                    bookmaker=book,
                    team_abbrev=abbrev,
                    opponent_abbrev=opponent,
                )
            )
            continue
        if market in TOTAL_TYPES and side_name in {"OVER", "UNDER"} and number is not None:
            offers.append(
                _offer(
                    game,
                    market_type="TOTAL",
                    market_key=f"{game.game_id}|TOTAL|{number:.1f}",
                    target=f"{game.away.abbrev} @ {game.home.abbrev}",
                    selection=f"{side_name.title()} {number:.1f}",
                    side=side_name,
                    line=number,
                    american=american,
                    bookmaker=book,
                )
            )
            continue
        prop = PROP_MARKETS.get(market)
        if prop is None and extended:
            prop = EXTRA_PROP_MARKETS.get(market)
            if prop == "GOALS" and (number is None or number <= 0.5):
                prop = "GOALSCORER"  # Over 0.5 goals is the anytime-goal bet
            if prop == "DOUBLE_DOUBLE":
                number = 0.5
        if prop is None:
            continue
        player_name = str(row.get("player_name") or "").strip()
        if not player_name:
            continue
        player = by_name.get(_norm_name(player_name))
        if player is None:
            player = PlayerProfile(player_id=_stable_player_id(player_name), name=player_name, team_abbrev="", position="")
        opponent = game.away.abbrev if player.team_abbrev == game.home.abbrev else game.home.abbrev
        if prop in YES_TYPES or (extended and prop == "DOUBLE_DOUBLE"):
            if side_name not in {"YES", "OVER"}:
                continue
            label = YES_LABELS.get(prop, "anytime TD")
            offers.append(
                _offer(
                    game,
                    market_type=prop,
                    market_key=f"{game.game_id}|{prop}|{player.player_id}|0.5",
                    target=player.name,
                    selection=f"{player.name} {label}",
                    side="YES",
                    line=0.5,
                    american=american,
                    bookmaker=book,
                    team_abbrev=player.team_abbrev or None,
                    opponent_abbrev=opponent,
                    player_id=player.player_id,
                )
            )
            continue
        if side_name not in {"OVER", "UNDER"} or number is None:
            continue
        offers.append(
            _offer(
                game,
                market_type=prop,
                market_key=f"{game.game_id}|{prop}|{player.player_id}|{number:.1f}",
                target=player.name,
                selection=f"{player.name} {side_name.title()} {number:.1f}",
                side=side_name,
                line=number,
                american=american,
                bookmaker=book,
                team_abbrev=player.team_abbrev or None,
                opponent_abbrev=opponent,
                player_id=player.player_id,
                selectable=side_name == "OVER",
            )
        )
    return dedupe_offers(offers)


def _wait_seconds(headers) -> float:
    """Seconds the API asks us to wait, from Retry-After or the rate-limit reset stamp."""
    try:
        retry = headers.get("retry-after")
        if retry is not None:
            return max(0.0, float(retry))
        reset = headers.get("x-ratelimit-reset")
        if reset is not None:
            return max(0.0, float(reset) - _now())
    except (TypeError, ValueError):
        pass
    return 0.0


def _pages(
    api_key: str,
    params: dict,
    *,
    max_pages: int = MAX_PAGES,
    deadline: float | None = None,
    strict: bool = True,
) -> tuple[list[dict], int]:
    """Follow the cursor until has_more is false. Returns (rows, pages requested).

    The free tier allows about 12 requests a minute, so a 429 or an empty quota waits
    for the reset the API reports instead of retrying right away. With strict=False a
    failure keeps the rows already collected.
    """
    rows: list[dict] = []
    cursor = None
    offset = None
    pages = 0
    throttled = 0
    # #region agent log
    from src.api.http_client import _dbg
    _t0 = time.monotonic()
    _dbg("F", "sharp_client.py:_pages", "pages start", {"market": params.get("market"), "events": len(str(params.get("event_id", "")).split(",")) if params.get("event_id") else 0})
    # #endregion
    while pages < max_pages:
        query = dict(params)
        query["limit"] = PAGE_LIMIT
        if cursor:
            query["cursor"] = cursor
        elif offset:
            query["offset"] = offset
        sink: dict = {}
        try:
            body = get_json(
                f"{SHARPAPI_BASE}/odds",
                params=query,
                ttl_seconds=PROPS_TTL if params.get("market") == "props" else 120,
                headers={"X-API-Key": api_key},
                header_sink=sink,
                attempts=1,
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429 and throttled < MAX_THROTTLE_WAITS:
                throttled += 1
                pause = min(max(_wait_seconds(exc.response.headers), 5.0) + 1.0, MAX_WAIT_SECONDS)
                if deadline is not None and _clock() + pause > deadline:
                    logger.warning("SharpAPI rate limit hit; stopping early after %s pages", pages)
                    break
                # #region agent log
                _dbg("F", "sharp_client.py:_pages", "429 sleep", {"pause_s": pause, "pages": pages, "elapsed_s": round(time.monotonic() - _t0, 1), "retry_after": exc.response.headers.get("retry-after")})
                # #endregion
                _sleep(pause)
                continue
            if strict:
                raise
            logger.warning("SharpAPI page failed: status %s", exc.response.status_code)
            break
        except (httpx.HTTPError, ValueError) as exc:
            if strict:
                raise
            logger.warning("SharpAPI page failed: %s", type(exc).__name__)
            break
        pages += 1
        if not isinstance(body, dict):
            break
        page = body.get("data") or []
        if not isinstance(page, list) or not page:
            break
        rows.extend(row for row in page if isinstance(row, dict))
        # #region agent log
        _dbg("F", "sharp_client.py:_pages", "page ok", {"page": pages, "rows": len(page), "total_rows": len(rows), "elapsed_s": round(time.monotonic() - _t0, 1), "remaining": sink.get("x-ratelimit-remaining"), "has_more": (body.get("pagination") or {}).get("has_more")})
        # #endregion
        pagination = body.get("pagination") or {}
        cursor = pagination.get("next_cursor")
        offset = pagination.get("next_offset") if not cursor else None
        if not pagination.get("has_more") or not (cursor or offset):
            break
        remaining = sink.get("x-ratelimit-remaining")
        if remaining is not None and str(remaining).isdigit():
            _quota["remaining"], _quota["at"] = int(remaining), _clock()
            if deadline is not None and int(remaining) <= QUOTA_RESERVE:
                logger.info("SharpAPI props paused to keep calls for the other leagues; more load on the next refresh")
                break
        if remaining is not None and str(remaining).isdigit() and int(remaining) <= 0:
            pause = min(_wait_seconds(sink) + 1.0, MAX_WAIT_SECONDS)
            if deadline is not None and _clock() + pause > deadline:
                logger.warning("SharpAPI quota used up; stopping early after %s pages", pages)
                break
            # #region agent log
            _dbg("F", "sharp_client.py:_pages", "quota sleep", {"pause_s": pause, "pages": pages, "elapsed_s": round(time.monotonic() - _t0, 1)})
            # #endregion
            _sleep(pause)
    # #region agent log
    _dbg("F", "sharp_client.py:_pages", "pages end", {"pages": pages, "rows": len(rows), "elapsed_s": round(time.monotonic() - _t0, 1)})
    # #endregion
    return rows, pages


def fetch_sharp_offers(
    games: list[Game], players: list[PlayerProfile], api_key: str, sport: str, extended: bool = False
) -> list[Offer]:
    """extended=True also parses EXTRA_PROP_MARKETS from the same pages (same requests, same cache)."""
    fetch_sharp_offers.last_note = ""
    league = LEAGUE.get(sport.upper(), sport.lower())
    main, _used = _pages(
        api_key,
        {
            "league": league,
            "market": MAIN_MARKETS.get(sport.upper(), MAIN_MARKETS["NBA"]),
            "is_main_line": "true",
            "include_futures": "false",
        },
    )
    offers = parse_sharp_rows(main, games, players, sport.upper(), extended)
    event_games: dict[str, Game] = {}
    for row in main:
        event_id = row.get("event_id")
        if not event_id or str(event_id) in event_games:
            continue
        game = _match_game(row, games)
        if game is not None:
            event_games[str(event_id)] = game
    if not event_games:
        return offers
    # Earliest games first, a few per request, so a stop for rate limits starves the last games, not random ones.
    ordered = sorted(event_games, key=lambda event: (event_games[event].start_time_utc, event))
    batches = [ordered[index : index + EVENTS_PER_BATCH] for index in range(0, len(ordered), EVENTS_PER_BATCH)]
    deadline = _clock() + PROPS_BUDGET_SECONDS
    pages_left = MAX_PAGES - 1
    props: list[dict] = []
    for batch in batches:
        # Quota seen within the last minute: do not spend what the other leagues' main lines need.
        age = _clock() - _quota["at"]
        low = _quota["remaining"] is not None and _quota["remaining"] <= QUOTA_RESERVE and 0 <= age < 60
        if low or pages_left <= 0 or _clock() >= deadline:
            logger.warning("SharpAPI props stopped early: some games keep synthetic prices")
            break
        rows, used = _pages(
            api_key,
            {
                "league": league,
                "event_id": ",".join(batch),
                "market": "props",
                "is_main_line": "true",
                "include_futures": "false",
            },
            max_pages=pages_left,
            deadline=deadline,
            strict=False,
        )
        props.extend(rows)
        pages_left -= used
    offers.extend(parse_sharp_rows(props, games, players, sport.upper(), extended))
    covered = {str(row.get("event_id")) for row in props}
    missing = sum(1 for event in event_games if event not in covered)
    if missing:
        fetch_sharp_offers.last_note = (
            f"Player props for {missing} of {len(event_games)} {sport.upper()} games are still loading "
            "(the free SharpAPI plan allows about 12 requests a minute). Estimated prices fill the gap. "
            "Press Refresh slate in a minute to load more."
        )
    return offers


fetch_sharp_offers.last_note = ""