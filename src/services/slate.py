"""Build the day's board from the NHL feed or the offline mock slate."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

from config import BOOK_LABELS, ODDS_API_KEY, ODDS_API_REGIONS
from src.api.data_models import PricedBoard, SlateContext, TeamProfile
from src.api.nhl_client import NHLClient, apply_rest, apply_team_summary, select_prop_players
from src.api.odds_client import (
    SYNTHETIC_BOOK,
    available_books,
    fetch_live_offers,
    merge_offers,
    offers_for_book,
    synthetic_offers,
)
from src.api.espn_client import fetch_scoreboard, fetch_standings
from src.engine.stats_analyzer import price_offers, slate_badges
from src.engine.team_board import LEAGUE_PPG, load_mock_team_context, price_team_offers, synthetic_team_offers
from src.utils.mock_data import load_mock_context

logger = logging.getLogger(__name__)


def load_board(
    slate_date: str,
    *,
    prefer_live: bool = True,
    odds_api_key: str | None = None,
    odds_region: str | None = None,
    sportsbook: str | None = None,
    sport: str = "NHL",
) -> PricedBoard:
    sport = sport.upper()
    if sport in {"NFL", "NBA"}:
        return _load_team_board(
            slate_date,
            sport,
            prefer_live=prefer_live,
            odds_api_key=odds_api_key,
            odds_region=odds_region,
            sportsbook=sportsbook,
        )
    key = odds_api_key if odds_api_key is not None else ODDS_API_KEY
    region = odds_region or ODDS_API_REGIONS
    context = _live_context(slate_date) if prefer_live else None
    if context is None:
        context = load_mock_context(slate_date)
    elif prefer_live and context.source == "nhl" and not context.notes:
        context.notes.append("Live NHL schedule, standings, and club stats.")

    synthetic = synthetic_offers(context)
    live: list = []
    if key:
        try:
            live = fetch_live_offers(context.games, context.players, key, region, sport="NHL")
        except Exception as exc:
            logger.warning("Odds feed failed: %s", exc)
            context.notes.append(f"Odds API request failed ({exc}). Synthetic prices are filling the board.")
    books = available_books(live) if live else [SYNTHETIC_BOOK]
    requested = sportsbook or "bet365"
    if requested not in books:
        requested = next((book for book in books if book != SYNTHETIC_BOOK), SYNTHETIC_BOOK)
    chosen = offers_for_book(live, requested)
    offers, odds_source = merge_offers(chosen, synthetic)
    label = BOOK_LABELS.get(requested, requested)
    if requested == SYNTHETIC_BOOK or odds_source.startswith("synthetic"):
        context.notes.append("Prices are the synthetic board. Pick a live sportsbook in the sidebar when the feed has one.")
    elif odds_source.startswith("mixed"):
        context.notes.append(f"Showing {label}. Markets that book did not post use the synthetic board.")
    else:
        context.notes.append(f"Showing {label} from the US, UK, and EU feeds.")
    legs = price_offers(context, offers)
    return PricedBoard(
        context=context,
        odds_source=requested if requested != SYNTHETIC_BOOK else odds_source,
        legs=legs,
        badges=slate_badges(context),
        available_books=books,
    )


def _load_team_board(
    slate_date: str,
    sport: str,
    *,
    prefer_live: bool,
    odds_api_key: str | None,
    odds_region: str | None,
    sportsbook: str | None,
) -> PricedBoard:
    key = odds_api_key if odds_api_key is not None else ODDS_API_KEY
    region = odds_region or ODDS_API_REGIONS
    context = _team_context(sport, slate_date) if prefer_live else None
    if context is None:
        context = load_mock_team_context(sport, slate_date)
    elif not context.notes:
        context.notes.append(f"Live {sport} scoreboard and standings.")
    synthetic = synthetic_team_offers(context)
    live: list = []
    if key:
        try:
            live = fetch_live_offers(context.games, context.players, key, region, sport=sport)
        except Exception as exc:
            logger.warning("%s odds feed failed: %s", sport, exc)
            context.notes.append(f"Odds API request failed ({exc}). Synthetic prices are filling the board.")
    books = available_books(live) if live else [SYNTHETIC_BOOK]
    requested = sportsbook or "bet365"
    if requested not in books:
        requested = next((book for book in books if book != SYNTHETIC_BOOK), SYNTHETIC_BOOK)
    chosen = offers_for_book(live, requested)
    offers, _source = merge_offers(chosen, synthetic)
    label = BOOK_LABELS.get(requested, requested)
    if requested == SYNTHETIC_BOOK:
        context.notes.append(f"{sport} prices are the synthetic board until a live sportsbook is available.")
    else:
        context.notes.append(f"Showing {label} {sport} lines. Missing markets use the synthetic board.")
    return PricedBoard(
        context=context,
        odds_source=requested if requested != SYNTHETIC_BOOK else SYNTHETIC_BOOK,
        legs=price_team_offers(context, offers),
        badges={},
        available_books=books,
    )


def _team_context(sport: str, slate_date: str) -> SlateContext | None:
    try:
        games = fetch_scoreboard(sport, slate_date)
        if not games:
            return None
        profiles = fetch_standings(sport)
        league = LEAGUE_PPG[sport]
        for game in games:
            for side in (game.home, game.away):
                profiles.setdefault(
                    side.abbrev,
                    TeamProfile(abbrev=side.abbrev, name=side.name, gf_per_game=league, ga_per_game=league, l10_gf_per_game=league, l10_ga_per_game=league),
                )
        return SlateContext(
            slate_date=slate_date,
            source="espn",
            sport=sport,
            games=games,
            teams={abbrev: profile for abbrev, profile in profiles.items() if abbrev in {side.abbrev for game in games for side in (game.home, game.away)}},
            players=[],
            goalies=[],
            notes=[],
        )
    except Exception as exc:
        logger.warning("%s scoreboard unavailable, using mock slate: %s", sport, exc)
        return None


def _live_context(slate_date: str) -> SlateContext | None:
    client = NHLClient()
    try:
        games, appearances, season = client.schedule(slate_date)
        if not games or season is None:
            return None
        profiles = client.standings()
        try:
            apply_team_summary(profiles, client.team_summary(season))
        except Exception as exc:
            logger.warning("Team summary unavailable: %s", exc)
        apply_rest(profiles, appearances, slate_date)
        playing = {game.home.abbrev for game in games} | {game.away.abbrev for game in games}
        profiles = {abbrev: profile for abbrev, profile in profiles.items() if abbrev in playing}

        players = []
        goalies = []

        def _club(abbrev: str):
            return abbrev, client.club_stats(abbrev, season)

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_club, abbrev) for abbrev in sorted(playing)]
            for future in as_completed(futures):
                try:
                    abbrev, (club_players, club_goalies) = future.result()
                except Exception as exc:
                    logger.warning("Club stats failed: %s", exc)
                    continue
                chosen = select_prop_players(club_players, limit=2)
                players.extend(chosen)
                starter = next((goalie for goalie in club_goalies if goalie.projected_starter), None)
                if starter is None and club_goalies:
                    starter = club_goalies[0]
                    starter.projected_starter = True
                if starter is not None:
                    goalies.append(starter)
        if players:
            client.enrich_game_logs(players, season)
        return SlateContext(
            slate_date=slate_date,
            source="nhl",
            games=games,
            teams=profiles,
            players=players,
            goalies=goalies,
            notes=[],
        )
    except Exception as exc:
        logger.warning("NHL feed unavailable, using mock slate: %s", exc)
        return None
