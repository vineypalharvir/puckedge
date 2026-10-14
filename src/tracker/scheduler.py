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


def run_settlement_once() -> int:
    client = NHLClient()

    def _fetch(game_id: str):
        return client.boxscore(game_id)

    session = open_session()
    try:
        return settle_pending(session, _fetch)
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
