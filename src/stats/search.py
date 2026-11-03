"""Find any NHL, NBA, or NFL player or team without using the odds board."""

from __future__ import annotations

import logging
from datetime import datetime

from src.api.http_client import get_json

logger = logging.getLogger(__name__)

SEARCH_URL = "https://site.web.api.espn.com/apis/common/v3/search"
OVERVIEW_URL = "https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{league}/athletes/{athlete_id}/overview"
GAMELOG_URL = "https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{league}/athletes/{athlete_id}/gamelog"
NHL_SEARCH = "https://search.d3.nhle.com/api/v1/search/player"
NHL_LOG = "https://api-web.nhle.com/v1/player/{player_id}/game-log/now"
NHL_LANDING = "https://api-web.nhle.com/v1/player/{player_id}/landing"

LEAGUES = {
    "nfl": ("NFL", "football"),
    "nba": ("NBA", "basketball"),
    "nhl": ("NHL", "hockey"),
}


def season_rows(gamelog: dict) -> list[dict]:
    """One row per regular season in the ESPN gamelog. Averages are per game."""
    names = gamelog.get("names") or []
    rows = []
    for season in gamelog.get("seasonTypes") or []:
        label = str(season.get("displayName") or "")
        text = label.lower()
        if "regular season" not in text or "play" in text:
            continue
        events = []
        for category in season.get("categories") or []:
            events = category.get("events") or events
        if not events:
            continue
        totals: dict[str, float] = {}
        for event in events:
            for index, raw in enumerate(event.get("stats") or []):
                if index >= len(names):
                    continue
                column = names[index]
                key = column.split("-", 1)[0]
                piece = str(raw).split("-", 1)[0].replace(",", "")
                try:
                    totals[key] = totals.get(key, 0.0) + float(piece)
                except ValueError:
                    continue
        games = len(events)
        rows.append({"season": label, "games": games, "totals": totals})
    return rows


def _last(name: str) -> str:
    parts = [part for part in name.lower().replace(".", " ").split() if len(part) > 1]
    return parts[-1] if parts else ""


def search_entities(query: str) -> list[dict]:
    text = query.strip()
    if len(text) < 2:
        return []
    found = []
    seen: set[str] = set()
    for kind_name in ("player", "team"):
        try:
            payload = get_json(SEARCH_URL, {"query": text, "limit": 12, "type": kind_name}, ttl_seconds=600)
        except Exception as exc:
            logger.warning("Search failed for %s: %s", text, exc)
            continue
        for item in payload.get("items") or []:
            league = str(item.get("league") or "").lower()
            if league not in LEAGUES:
                continue
            sport, espn_sport = LEAGUES[league]
            kind = "team" if str(item.get("type") or kind_name).lower() == "team" else "player"
            identity = f"{kind}:{item.get('id')}"
            if identity in seen:
                continue
            seen.add(identity)
            relations = item.get("teamRelationships") or []
            core = (relations[0].get("core") or {}) if relations and isinstance(relations[0], dict) else {}
            position = item.get("position")
            position_name = position.get("abbreviation") if isinstance(position, dict) else str(position or "")
            found.append(
                {
                    "kind": kind,
                    "name": str(item.get("displayName") or text),
                    "sport": sport,
                    "league": league,
                    "espn_sport": espn_sport,
                    "espn_id": str(item.get("id") or ""),
                    "team": str(core.get("abbreviation") or item.get("abbreviation") or ""),
                    "position": position_name,
                }
            )
    return [row for row in found if row["espn_id"]][:8]


def _nhl_identity(name: str) -> tuple[str, str] | None:
    try:
        payload = get_json(NHL_SEARCH, {"culture": "en-us", "limit": 5, "q": name}, ttl_seconds=3600)
    except Exception as exc:
        logger.warning("NHL search failed for %s: %s", name, exc)
        return None
    rows = payload if isinstance(payload, list) else []
    last = _last(name)
    for row in rows:
        if _last(str(row.get("name") or "")) == last:
            return str(row.get("playerId") or ""), str(row.get("positionCode") or "")
    return None


def load_player(entity: dict) -> dict:
    overview: dict = {}
    gamelog: dict = {}
    if entity.get("espn_id"):
        url_kwargs = {"sport": entity["espn_sport"], "league": entity["league"], "athlete_id": entity["espn_id"]}
        try:
            overview = get_json(OVERVIEW_URL.format(**url_kwargs), ttl_seconds=1800)
        except Exception as exc:
            logger.warning("Overview failed for %s: %s", entity.get("name"), exc)
        try:
            gamelog = get_json(GAMELOG_URL.format(**url_kwargs), ttl_seconds=1800)
        except Exception as exc:
            logger.warning("Gamelog failed for %s: %s", entity.get("name"), exc)
    nhl_log: dict = {}
    nhl_id = ""
    position = str(entity.get("position") or "")
    if entity.get("league") == "nhl":
        identity = _nhl_identity(str(entity.get("name") or ""))
        if identity and identity[0]:
            nhl_id = identity[0]
            position = position or identity[1]
            try:
                nhl_log = get_json(NHL_LOG.format(player_id=identity[0]), ttl_seconds=1800)
            except Exception as exc:
                logger.warning("NHL log failed for %s: %s", entity.get("name"), exc)
    events = ((overview.get("nextGame") or {}).get("league") or {}).get("events") or []
    matchup = str(events[0].get("shortName") or "") if events else ""
    return {"overview": overview, "gamelog": gamelog, "nhl": nhl_log, "nhl_id": nhl_id, "position": position, "matchup": matchup}


def load_nhl_landing(player_id: str) -> dict:
    if not player_id:
        return {}
    try:
        payload = get_json(NHL_LANDING.format(player_id=player_id), ttl_seconds=1800)
    except Exception as exc:
        logger.warning("NHL landing failed for %s: %s", player_id, exc)
        return {}
    return payload if isinstance(payload, dict) else {}


SKATER_TILES = (
    ("gamesPlayed", "GP"),
    ("goals", "G"),
    ("assists", "A"),
    ("points", "P"),
    ("plusMinus", "+/-"),
    ("pim", "PIM"),
    ("shots", "SOG"),
    ("powerPlayGoals", "PPG"),
)
GOALIE_TILES = (
    ("gamesPlayed", "GP"),
    ("wins", "W"),
    ("losses", "L"),
    ("otLosses", "OTL"),
    ("savePctg", "SV%"),
    ("goalsAgainstAvg", "GAA"),
    ("shutouts", "SO"),
)
SKATER_PER_GAME = (("goals", "G/GP"), ("assists", "A/GP"), ("points", "P/GP"), ("shots", "SOG/GP"))
SKATER_GAME_COLUMNS = (
    ("goals", "G"),
    ("assists", "A"),
    ("points", "P"),
    ("plusMinus", "+/-"),
    ("pim", "PIM"),
    ("powerPlayGoals", "PPG"),
    ("shorthandedGoals", "SHG"),
    ("shots", "S"),
    ("shifts", "SHIFT"),
    ("toi", "TOI"),
)
GOALIE_GAME_COLUMNS = (
    ("decision", "DEC"),
    ("shotsAgainst", "SA"),
    ("goalsAgainst", "GA"),
    ("savePctg", "SV%"),
    ("toi", "TOI"),
)


def _toi_seconds(text) -> int | None:
    parts = str(text or "").split(":")
    if len(parts) != 2:
        return None
    try:
        return int(parts[0]) * 60 + int(parts[1])
    except ValueError:
        return None


def _clock(seconds: float) -> str:
    whole = int(round(seconds))
    return f"{whole // 60}:{whole % 60:02d}"


def _stat_text(key: str, value) -> str:
    if key == "savePctg":
        return f"{float(value):.3f}".lstrip("0")
    if key == "goalsAgainstAvg":
        return f"{float(value):.2f}"
    if key == "plusMinus":
        number = int(value)
        return f"+{number}" if number > 0 else str(number)
    if isinstance(value, float) and not value.is_integer():
        return f"{value:.1f}"
    if isinstance(value, (int, float)):
        return f"{int(value):,}"
    return str(value)


def _tiles(stats: dict, layout: tuple[tuple[str, str], ...]) -> list[tuple[str, str]]:
    return [(label, _stat_text(key, stats[key])) for key, label in layout if stats.get(key) is not None]


def _short_date(raw: str) -> str:
    try:
        day = datetime.strptime(str(raw)[:10], "%Y-%m-%d")
    except ValueError:
        return str(raw)
    return f"{day:%b} {day.day}"


def _season_label(season) -> str:
    text = str(season or "")
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[6:]}"
    return text


def _season_toi(landing: dict, season) -> str:
    """GP-weighted TOI/GP across every NHL regular-season stint (a trade splits the season)."""
    stints = [
        row
        for row in landing.get("seasonTotals") or []
        if row.get("season") == season and row.get("gameTypeId") == 2 and row.get("leagueAbbrev") == "NHL"
    ]
    games = 0
    seconds = 0
    for row in stints:
        per_game = _toi_seconds(row.get("avgToi"))
        played = int(row.get("gamesPlayed") or 0)
        if per_game is None or played <= 0:
            return ""
        games += played
        seconds += per_game * played
    return _clock(seconds / games) if games else ""


def nhl_profile(landing: dict) -> dict:
    """Season totals, per-game rates, career totals, and last 5 games from the NHL landing feed."""
    featured = landing.get("featuredStats") or {}
    regular = featured.get("regularSeason") or {}
    season_stats = regular.get("subSeason") or {}
    career_stats = (landing.get("careerTotals") or {}).get("regularSeason") or regular.get("career") or {}
    goalie = str(landing.get("position") or "").upper() == "G" or "savePctg" in season_stats or "savePctg" in career_stats
    tiles = GOALIE_TILES if goalie else SKATER_TILES

    season = _tiles(season_stats, tiles)
    per_game: list[tuple[str, str]] = []
    if season and not goalie:
        toi = _season_toi(landing, featured.get("season"))
        if toi:
            season.append(("TOI/GP", toi))
        played = int(season_stats.get("gamesPlayed") or 0)
        if played > 0:
            per_game = [
                (label, f"{float(season_stats[key]) / played:.2f}")
                for key, label in SKATER_PER_GAME
                if season_stats.get(key) is not None
            ]

    career = _tiles(career_stats, tiles)
    if career and not goalie and _toi_seconds(career_stats.get("avgToi")) is not None:
        career.append(("TOI/GP", str(career_stats["avgToi"])))

    columns = GOALIE_GAME_COLUMNS if goalie else SKATER_GAME_COLUMNS
    games = landing.get("last5Games") or []
    shown = [(key, label) for key, label in columns if any(game.get(key) is not None for game in games)]
    rows = []
    for game in games:
        where = "vs" if game.get("homeRoadFlag") == "H" else "@"
        row = [_short_date(game.get("gameDate", "")), f"{where} {game.get('opponentAbbrev', '')}".strip()]
        row += ["" if game.get(key) is None else _stat_text(key, game[key]) for key, _ in shown]
        rows.append(row)

    return {
        "season": f"{_season_label(featured.get('season'))} Season" if featured.get("season") else "This season",
        "season_tiles": season,
        "per_game": per_game,
        "career": career,
        "games": {"columns": ["Date", "Matchup"] + [label for _, label in shown], "rows": rows} if rows else {},
    }


ESPN_STATS = (
    ("minutes", "MIN"),
    ("points", "PTS"),
    ("totalRebounds", "REB"),
    ("assists", "AST"),
    ("threePointFieldGoalsMade", "3PM"),
    ("steals", "STL"),
    ("blocks", "BLK"),
    ("turnovers", "TO"),
    ("completions", "CMP"),
    ("passingAttempts", "ATT"),
    ("passingYards", "PASS YDS"),
    ("passingTouchdowns", "PASS TD"),
    ("interceptions", "INT"),
    ("rushingAttempts", "CAR"),
    ("rushingYards", "RUSH YDS"),
    ("rushingTouchdowns", "RUSH TD"),
    ("receivingTargets", "TGT"),
    ("receptions", "REC"),
    ("receivingYards", "REC YDS"),
    ("receivingTouchdowns", "REC TD"),
    ("goals", "G"),
    ("shotsTotal", "SOG"),
)


def _count(value: float) -> str:
    return f"{int(value):,}" if float(value).is_integer() else f"{value:,.1f}"


def espn_profile(gamelog: dict) -> dict:
    """Latest regular season from an ESPN gamelog: totals, per-game averages, and last 5 games."""
    rows = season_rows(gamelog)
    if not rows:
        return {}
    latest = rows[0]
    games = int(latest["games"])
    totals = latest["totals"]
    present = [(key, label) for key, label in ESPN_STATS if key in totals]
    season = [("GP", str(games))] + [(label, _count(totals[key])) for key, label in present if key != "minutes"]
    per_game = [(f"{label}/G", f"{totals[key] / games:.1f}") for key, label in present] if games else []

    names = gamelog.get("names") or []
    positions = {name.split("-", 1)[0]: index for index, name in enumerate(names)}
    recent_keys = [(key, label) for key, label in present if key in positions][:7]
    meta = gamelog.get("events") or {}
    events = []
    for season_type in gamelog.get("seasonTypes") or []:
        if str(season_type.get("displayName") or "") != latest["season"]:
            continue
        for category in season_type.get("categories") or []:
            events = category.get("events") or events
    events = sorted(
        events,
        key=lambda event: str((meta.get(str(event.get("eventId") or "")) or {}).get("gameDate") or ""),
        reverse=True,
    )
    table = []
    for event in events[:5]:
        info = meta.get(str(event.get("eventId") or "")) or {}
        values = event.get("stats") or []
        opponent = (info.get("opponent") or {}).get("abbreviation") or ""
        where = "@" if info.get("atVs") == "@" else "vs"
        result = " ".join(part for part in (info.get("gameResult"), info.get("score")) if part)
        row = [_short_date(info.get("gameDate", "")), f"{where} {opponent}" if opponent else "", result]
        for key, _ in recent_keys:
            index = positions[key]
            row.append(str(values[index]).split("-", 1)[0] if index < len(values) else "")
        table.append(row)
    columns = ["Date", "Matchup", "Result"] + [label for _, label in recent_keys]
    if table and not any(row[2] for row in table):
        columns.pop(2)
        table = [row[:2] + row[3:] for row in table]
    return {
        "season": latest["season"],
        "season_tiles": season,
        "per_game": per_game,
        "career": [],
        "games": {"columns": columns, "rows": table} if table else {},
        "past": rows[1:],
    }
