"""Fifteen-minute settlement loop."""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from config import SETTLEMENT_INTERVAL_MINUTES
from src.api.nhl_client import NHLClient
from src.tracker.database import open_session
from src.tracker.repository import settle_pending

logger = logging.getLogger(__name__)
_scheduler: BackgroundScheduler | None = None


def _espn_games(sport: str, day: str):
    from src.api.espn_client import ESPN_PATH, SCOREBOARD, parse_scoreboard
    from src.api.http_client import get_json

    payload = get_json(
        SCOREBOARD.format(path=ESPN_PATH[sport]),
        params={"dates": day.replace("-", "")},
        ttl_seconds=900,
    )
    return parse_scoreboard(payload, day)


def find_game_id(sport: str, away: str, home: str, days: list[str]) -> str | None:
    """Look a matchup up on the league schedule for the given days."""
    client = NHLClient()
    for day in days:
        if sport == "NHL":
            games, _appearances, _season = client.schedule(day)
        else:
            games = _espn_games(sport, day)
        for game in games:
            if game.away.abbrev.upper() == away and game.home.abbrev.upper() == home:
                return game.game_id
    return None


def run_settlement_once() -> int:
    from src.api.espn_client import fetch_summary_box

    client = NHLClient()

    def _fetch(game_id: str, sport: str = "NHL"):
        if sport in {"NFL", "NBA"}:
            return fetch_summary_box(sport, game_id)
        return client.boxscore(game_id)

    session = open_session()
    try:
        return settle_pending(session, _fetch, find_game_id)
    finally:
        session.close()


def start_scheduler() -> BackgroundScheduler | None:
    global _scheduler
    if _scheduler is not None and _scheduler.running:
        return _scheduler
    scheduler = BackgroundScheduler(daemon=True)
    scheduler.add_job(
        _safe_settle,
        "interval",
        minutes=SETTLEMENT_INTERVAL_MINUTES,
        id="puckedge-settle",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    _scheduler = scheduler
    return scheduler


def _safe_settle() -> None:
    try:
        graded = run_settlement_once()
        if graded:
            logger.info("Auto-settled %s ticket(s).", graded)
    except Exception:
        logger.exception("Auto-settlement failed")
