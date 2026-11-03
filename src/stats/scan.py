"""Game-log fetching for the OmniScreener: ESPN search + gamelog, a few players per press.

Why ESPN only: api-web.nhle.com allows about 55 requests a minute and the Studio page already
spends from that budget, so hundreds of per-player NHL calls would trigger the 429 cooldown for
the whole app. ESPN's gamelog covers NHL, NBA and NFL in one shape. Every response goes through
get_json, so repeat visits within 3 hours read the disk cache and cost nothing.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import httpx

from src.api.http_client import get_json
from src.engine.screener import PlayerSeries, norm_name

logger = logging.getLogger(__name__)

SEARCH_URL = "https://site.web.api.espn.com/apis/common/v3/search"
GAMELOG_URL = "https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{league}/athletes/{athlete_id}/gamelog"
SCAN_TTL = 3 * 3600
DEFAULT_BUDGET = 25  # players per press or auto step
MAX_WORKERS = 4
LEAGUES = {"NHL": ("hockey", "nhl"), "NBA": ("basketball", "nba"), "NFL": ("football", "nfl")}

# ESPN column name -> canonical key, per league. A made-attempted cell ("3-8") counts the makes.
COLUMNS = {
    "NHL": {"goals": "goals", "assists": "assists", "points": "points", "shotsTotal": "shots", "saves": "saves", "shotsAgainst": "shots_against", "goalsAgainst": "goals_against"},
    "NBA": {
        "points": "points",
        "totalRebounds": "rebounds",
        "rebounds": "rebounds",
        "assists": "assists",
        "threePointFieldGoalsMade": "threes",
        "steals": "steals",
        "blocks": "blocks",
    },
    "NFL": {
        "passingYards": "pass_yds",
        "passingTouchdowns": "pass_td",
        "rushingYards": "rush_yds",
        "rushingAttempts": "rush_att",
        "rushingTouchdowns": "rush_td",
        "receivingYards": "rec_yds",
        "receptions": "receptions",
        "receivingTouchdowns": "rec_td",
    },
}


class ScanLimited(Exception):
    """The host answered 429. Stop scanning for now."""


def _number(text) -> float:
    cleaned = str(text).strip().replace(",", "")
    if cleaned.startswith("-"):
        cleaned = "0" if len(cleaned) == 1 else cleaned  # "-" means no entry; "-3" is a real negative
    else:
        cleaned = cleaned.split("-", 1)[0]  # "3-8" made-attempted counts the makes
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _clock_seconds(text) -> int | None:
    parts = str(text or "").split(":")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def series_from_espn(sport: str, name: str, payload: dict, *, team: str = "", position: str = "") -> PlayerSeries | None:
    """Current regular season, one value per game actually played, most recent first."""
    sport = sport.upper()
    if not isinstance(payload, dict):
        return None
    names = payload.get("names") or []
    seasons = payload.get("seasonTypes") or []
    regular = next((item for item in seasons if "regular season" in str(item.get("displayName") or "").lower() and "play" not in str(item.get("displayName") or "").lower()), None)
    if regular is None or not names:
        return None
    wanted = {}
    for index, column in enumerate(names):
        base = str(column).split("-", 1)[0]
        key = COLUMNS.get(sport, {}).get(base)
        if key is not None and key not in wanted.values():
            wanted[index] = key
    if not wanted:
        return None
    minutes_at = next((index for index, column in enumerate(names) if column == "minutes"), None)
    toi_at = next((index for index, column in enumerate(names) if column == "timeOnIcePerGame"), None)
    meta = payload.get("events") or {}
    events: dict[str, dict] = {}
    for category in regular.get("categories") or []:
        for event in category.get("events") or []:
            event_id = str(event.get("eventId") or "")
            if event_id and event_id not in events:
                events[event_id] = event
    rows = []
    for event_id, event in events.items():
        cells = event.get("stats") or []
        if not cells:
            continue
        if minutes_at is not None and minutes_at < len(cells) and _number(cells[minutes_at]) <= 0:
            continue  # did not play
        if sport == "NHL" and toi_at is not None and toi_at < len(cells):
            seconds = _clock_seconds(cells[toi_at])
            if seconds is not None and seconds <= 0:
                continue
        if sport == "NFL" and all(str(cell).strip() in {"-", ""} for cell in cells):
            continue
        date = str((meta.get(event_id) or {}).get("gameDate") or "")
        row = {key: _number(cells[index]) for index, key in wanted.items() if index < len(cells)}
        rows.append((date, event_id, row))
    if not rows:
        return None
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    stats: dict[str, list[float]] = {}
    for key in wanted.values():
        stats[key] = [row.get(key, 0.0) for _date, _event, row in rows]
    if sport == "NHL" and "shots_against" in stats and "goals_against" in stats and "saves" not in stats:
        stats["saves"] = [max(0.0, shots - goals) for shots, goals in zip(stats["shots_against"], stats["goals_against"])]
    return PlayerSeries(sport=sport, name=name, team=team, position=position, stats=stats)


def find_athletes(sport: str, name: str) -> list[dict]:
    """Every ESPN athlete in the right league whose full name matches (namesakes exist)."""
    league = LEAGUES[sport.upper()][1]
    payload = get_json(SEARCH_URL, {"query": name, "limit": 8, "type": "player"}, ttl_seconds=SCAN_TTL)
    items = [item for item in (payload or {}).get("items") or [] if str(item.get("league") or "").lower() == league]
    target = norm_name(name)
    return [item for item in items if norm_name(str(item.get("displayName") or "")) == target and item.get("id")]


def find_athlete(sport: str, name: str) -> dict | None:
    """The first ESPN athlete whose full name matches, in the right league. None when not found."""
    matches = find_athletes(sport, name)
    return matches[0] if matches else None


def fetch_player_series(sport: str, name: str) -> PlayerSeries | None:
    """Look the player up and read the current-season game log. Raises ScanLimited on a 429.

    A namesake with no game log (404) is skipped and the next match is tried; when none has a log
    the player is reported as having no log instead of an error.
    """
    athlete: dict | None = None
    payload = None
    try:
        espn_sport, league = LEAGUES[sport.upper()]
        for candidate in find_athletes(sport, name):
            try:
                payload = get_json(GAMELOG_URL.format(sport=espn_sport, league=league, athlete_id=candidate["id"]), ttl_seconds=SCAN_TTL)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    continue
                raise
            athlete = candidate
            break
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 429:
            raise ScanLimited() from exc
        raise
    if athlete is None or payload is None:
        return None
    relations = athlete.get("teamRelationships") or []
    core = (relations[0].get("core") or {}) if relations and isinstance(relations[0], dict) else {}
    position = athlete.get("position")
    position_name = position.get("abbreviation") if isinstance(position, dict) else str(position or "")
    return series_from_espn(sport, name, payload, team=str(core.get("abbreviation") or ""), position=str(position_name or ""))


def scan_batch(
    targets: list[tuple[str, str]],
    *,
    workers: int = MAX_WORKERS,
    budget: int = DEFAULT_BUDGET,
    progress: Callable[[int, int], None] | None = None,
    fetch: Callable[[str, str], PlayerSeries | None] | None = None,
) -> tuple[dict[tuple[str, str], PlayerSeries | None], dict[tuple[str, str], int], str]:
    """Fetch up to `budget` players with `workers` threads.

    Returns (results, errors, status). results maps (sport, normalized name) to a series, or to
    None when the player has no log (not retried). errors counts failures that should be retried
    once. status is "done", "limited" (a 429 stopped the batch) or "empty".
    """
    fetch = fetch or fetch_player_series
    chosen = targets[: max(0, budget)]
    if not chosen:
        return {}, {}, "empty"
    results: dict[tuple[str, str], PlayerSeries | None] = {}
    errors: dict[tuple[str, str], int] = {}
    stop = threading.Event()

    def work(sport: str, name: str):
        if stop.is_set():
            return sport, name, "skipped", None
        try:
            return sport, name, "ok", fetch(sport, name)
        except ScanLimited:
            stop.set()
            return sport, name, "limited", None
        except Exception as exc:  # network or parse failure: retry once on a later step
            logger.warning("Game log scan failed for %s: %s", name, type(exc).__name__)
            return sport, name, "error", None

    status = "done"
    done = 0
    with ThreadPoolExecutor(max_workers=max(1, min(workers, MAX_WORKERS))) as pool:
        futures = [pool.submit(work, sport, name) for sport, name in chosen]
        for future in as_completed(futures):
            sport, name, outcome, series = future.result()
            key = (sport.upper(), norm_name(name))
            if outcome == "ok":
                results[key] = series
            elif outcome == "error":
                errors[key] = errors.get(key, 0) + 1
            elif outcome == "limited":
                status = "limited"
            done += 1
            if progress is not None:
                progress(done, len(chosen))
    return results, errors, status


def espn_cooldown_seconds() -> int:
    """Seconds left on the NHL API 429 cooldown (the Studio uses that host), 0 when clear."""
    try:
        from src.api import http_client

        left = http_client._cooldown_until.get("nhle.com", 0.0) - time.time()
    except Exception:
        return 0
    return max(0, int(left))
