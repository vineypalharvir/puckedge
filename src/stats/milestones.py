"""Fetch the per-game stat series the 100% hit-rate scanner needs, one game at a time.

Nothing here runs at board load. A scan happens when the user opens a game's card (or presses
Scan). Every call goes through the cached `get_json` (game logs are kept for 3 hours), at most
four requests run at once, and a rate-limited host returns what is already saved and a plain note.

Sources (all public, no invented numbers):
- NHL: the NHL stats REST game reports for the team (skater summary for shots and points, skater
  realtime for hits and blocked shots, goalie summary for saves). One request per report per team
  returns every skater's every game, so a game card costs about six requests.
- NBA and NFL: the ESPN per-athlete game log, for the players that have a posted prop (or the team
  leaders when no props are posted). ESPN rosters map names to ids.
"""

from __future__ import annotations

import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Iterable

import httpx

from config import NHL_STATS_BASE
from src.api.data_models import MarketLeg
from src.api.espn_client import ESPN_PATH, SUMMARY
from src.api.http_client import RateLimited, get_json
from src.engine.hit_rates import (
    DefenseRank,
    GameData,
    PlayerSeries,
    norm_name,
    rank_table,
    specs_for,
)
from src.stats.books import SKIPPED_BOOKS

logger = logging.getLogger(__name__)

MAX_WORKERS = 4
MAX_PER_TEAM = 10
GAMELOG_TTL = 10800  # three hours
TABLE_TTL = 21600  # six hours for roster and league tables
SUMMARY_TTL = 900

ESPN_SITE = "https://site.api.espn.com/apis/site/v2/sports/{path}"
ESPN_ROSTER = ESPN_SITE + "/teams/{team_id}/roster"
ESPN_TEAMS = ESPN_SITE + "/teams"
ESPN_TEAM_STATS = ESPN_SITE + "/teams/{team_id}/statistics"
ESPN_GAMELOG = "https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{league}/athletes/{athlete_id}/gamelog"
ESPN_KIND = {"NBA": ("basketball", "nba"), "NFL": ("football", "nfl")}

# stat key -> ESPN gamelog column names that hold it (first one present wins)
ESPN_COLUMNS: dict[str, dict[str, tuple[str, ...]]] = {
    "NBA": {
        "POINTS": ("points",),
        "ASSISTS": ("assists",),
        "REBOUNDS": ("totalRebounds", "rebounds"),
        "THREES": ("threePointFieldGoalsMade-threePointFieldGoalsAttempted", "threePointFieldGoalsMade"),
    },
    "NFL": {
        "RUSH_YDS": ("rushingYards",),
        "RECEPTIONS": ("receptions",),
        "PASS_YDS": ("passingYards",),
        "PASS_TD": ("passingTouchdowns",),
    },
}

# NFL stat key -> (ESPN opponent category, stat name, noun for the note)
NFL_DEFENSE = {
    "RUSH_YDS": ("rushing", "rushingYards", "rushing yards"),
    "PASS_YDS": ("passing", "passingYards", "passing yards"),
    "RECEPTIONS": ("receiving", "receptions", "receptions"),
    "PASS_TD": ("passing", "passingTouchdowns", "passing touchdowns"),
}

OUT_WORDS = ("out", "injured reserve", "suspen")

Progress = Callable[[float, str], None]


# --------------------------------------------------------------------------- shared helpers


def season_floor(game_date: str) -> str:
    """ISO date before which a game belongs to a previous season (August 1 of the season's start year)."""
    try:
        year, month = int(game_date[:4]), int(game_date[5:7])
    except (TypeError, ValueError):
        return "0000-00-00"
    start = year if month >= 8 else year - 1
    return f"{start}-08-01"


def nhl_season_id(game_id: str, game_date: str) -> int:
    text = str(game_id or "")
    if len(text) >= 4 and text[:4].isdigit() and int(text[:4]) > 2000:
        year = int(text[:4])
    else:
        year = int(season_floor(game_date)[:4])
    return int(f"{year}{year + 1}")


def _is_rate_limited(exc: BaseException) -> bool:
    if isinstance(exc, RateLimited):
        return True
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


def _number(raw) -> float | None:
    text = str(raw if raw is not None else "").replace(",", "").strip()
    if not text or text in {"--", "-"}:
        return None
    if "-" in text[1:]:
        text = text.split("-", 1)[0]
    try:
        return max(float(text), 0.0)
    except ValueError:
        return None


def offered_names(legs: Iterable[MarketLeg], sport: str, game_id: str) -> Counter:
    """Players with a real posted prop in a scanned market, with how many markets each has."""
    markets = {spec.market for spec in specs_for(sport) if spec.market}
    seen: dict[str, set[str]] = {}
    display: dict[str, str] = {}
    for leg in legs:
        if leg.game_id != game_id or leg.market_type not in markets or leg.bookmaker in SKIPPED_BOOKS:
            continue
        key = norm_name(leg.target_entity)
        if not key:
            continue
        seen.setdefault(key, set()).add(leg.market_type)
        display.setdefault(key, leg.target_entity)
    return Counter({key: len(kinds) for key, kinds in seen.items()})


# --------------------------------------------------------------------------- NHL


def _nhl_rows(report: str, team_id: int, season: int) -> list[dict]:
    payload = get_json(
        f"{NHL_STATS_BASE}/{report}",
        params={
            "isAggregate": "false",
            "isGame": "true",
            "limit": -1,
            "start": 0,
            "cayenneExp": f"gameTypeId=2 and seasonId={season} and teamId={team_id}",
        },
        ttl_seconds=GAMELOG_TTL,
    )
    return list(payload.get("data") or []) if isinstance(payload, dict) else []


def _by_date(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: (str(row.get("gameDate") or ""), int(row.get("gameId") or 0)), reverse=True)


def nhl_skater_series(
    team: str,
    opponent: str,
    summary: list[dict],
    realtime: list[dict] | None,
    offered: Counter | None = None,
    limit: int = MAX_PER_TEAM,
) -> list[PlayerSeries]:
    """Skater series from the NHL per-game reports. Hits and blocks need the realtime report."""
    offered = offered or Counter()
    players: dict[int, list[dict]] = {}
    for row in summary:
        if row.get("playerId") is not None:
            players.setdefault(int(row["playerId"]), []).append(row)
    team_last = max((str(row.get("gameDate") or "") for row in summary), default="")
    extras: dict[tuple[int, int], dict] = {}
    for row in realtime or []:
        if row.get("playerId") is not None and row.get("gameId") is not None:
            extras[(int(row["playerId"]), int(row["gameId"]))] = row

    def usage(rows: list[dict]) -> float:
        return sum(float(row.get("timeOnIcePerGame") or 0) for row in rows) / max(len(rows), 1)

    ranked = sorted(players.items(), key=lambda item: usage(item[1]), reverse=True)
    chosen = [item for item in ranked if offered.get(norm_name(str(item[1][0].get("skaterFullName") or "")))]
    for item in ranked:
        if len(chosen) >= limit:
            break
        if item not in chosen:
            chosen.append(item)
    series: list[PlayerSeries] = []
    for player_id, rows in chosen[:limit]:
        rows = _by_date(rows)
        stats = {
            "SOG": [float(row.get("shots") or 0) for row in rows],
            "POINTS": [float(row.get("points") or 0) for row in rows],
        }
        if realtime is not None:
            joined = [extras.get((player_id, int(row.get("gameId") or 0))) for row in rows]
            if joined and all(item is not None for item in joined):
                stats["HITS"] = [float(item.get("hits") or 0) for item in joined]
                stats["BLOCKS"] = [float(item.get("blockedShots") or 0) for item in joined]
        series.append(
            PlayerSeries(
                name=str(rows[0].get("skaterFullName") or ""),
                sport="NHL",
                team=team,
                opponent=opponent,
                player_id=str(player_id),
                position=str(rows[0].get("positionCode") or ""),
                stats=stats,
                last_played=str(rows[0].get("gameDate") or ""),
                team_last_game=team_last,
            )
        )
    return series


def nhl_goalie_series(team: str, opponent: str, rows: list[dict]) -> PlayerSeries | None:
    """The goalie with the most starts, using only games he started. Tonight's starter is not known pre-game."""
    goalies: dict[int, list[dict]] = {}
    for row in rows:
        if row.get("playerId") is not None and int(row.get("gamesStarted") or 0) >= 1:
            goalies.setdefault(int(row["playerId"]), []).append(row)
    if not goalies:
        return None
    team_last = max((str(row.get("gameDate") or "") for row in rows), default="")
    player_id, starts = max(goalies.items(), key=lambda item: (len(item[1]), max(str(r.get("gameDate") or "") for r in item[1])))
    starts = _by_date(starts)
    saves = [float(row.get("saves") or 0) for row in starts]
    return PlayerSeries(
        name=str(starts[0].get("goalieFullName") or ""),
        sport="NHL",
        team=team,
        opponent=opponent,
        player_id=str(player_id),
        position="G",
        stats={"SAVES": saves},
        last_played=str(starts[0].get("gameDate") or ""),
        team_last_game=team_last,
        note="Projected starter (most starts); tonight's starter is not confirmed",
    )


def nhl_shot_tables(season: int) -> dict[str, dict[str, DefenseRank]]:
    """League-wide ranks: goals against (standings) and shots for/against (team summary). Both are cached."""
    from src.api.nhl_client import NHLClient, apply_team_summary

    client = NHLClient()
    profiles = client.standings()
    apply_team_summary(profiles, client.team_summary(season))
    played = {abbrev: team for abbrev, team in profiles.items() if team.games_played}
    return {
        "SOG": rank_table({abbrev: team.shots_against_per_game for abbrev, team in played.items()}, verb="allows", noun="shots"),
        "POINTS": rank_table({abbrev: team.ga_per_game for abbrev, team in played.items()}, verb="allows", noun="goals"),
        "SAVES": rank_table({abbrev: team.shots_for_per_game for abbrev, team in played.items()}, verb="generates", noun="shots"),
    }


# --------------------------------------------------------------------------- ESPN (NBA and NFL)


def roster_index(payload: dict) -> dict[str, dict]:
    """norm name -> {id, name, position} from an ESPN roster (flat list or grouped by unit)."""
    found: dict[str, dict] = {}
    athletes = payload.get("athletes") if isinstance(payload, dict) else None
    flat: list[dict] = []
    for entry in athletes or []:
        if isinstance(entry, dict) and isinstance(entry.get("items"), list):
            flat.extend(item for item in entry["items"] if isinstance(item, dict))
        elif isinstance(entry, dict):
            flat.append(entry)
    for item in flat:
        name = str(item.get("fullName") or item.get("displayName") or "")
        if not name or not item.get("id"):
            continue
        position = (item.get("position") or {}).get("abbreviation") if isinstance(item.get("position"), dict) else ""
        found[norm_name(name)] = {"id": str(item["id"]), "name": name, "position": str(position or "")}
    return found


def summary_context(payload: dict) -> dict:
    """Injury statuses, spread, and team leaders from the ESPN game summary."""
    status: dict[str, str] = {}
    for team in payload.get("injuries") or []:
        for item in team.get("injuries") or []:
            athlete = item.get("athlete") or {}
            name = str(athlete.get("displayName") or athlete.get("fullName") or "")
            if name and item.get("status"):
                status[norm_name(name)] = str(item["status"])
    spread = None
    source = ""
    for pick in payload.get("pickcenter") or []:
        if pick.get("spread") is not None:
            try:
                spread = abs(float(pick["spread"]))
                source = str((pick.get("provider") or {}).get("name") or "ESPN")
                break
            except (TypeError, ValueError):
                continue
    leaders: dict[str, list[tuple[str, str]]] = {}
    for block in payload.get("leaders") or []:
        abbrev = str((block.get("team") or {}).get("abbreviation") or "")
        for category in block.get("leaders") or []:
            for leader in category.get("leaders") or []:
                athlete = leader.get("athlete") or {}
                if athlete.get("id") and athlete.get("displayName"):
                    pair = (str(athlete["id"]), str(athlete["displayName"]))
                    if pair not in leaders.setdefault(abbrev, []):
                        leaders[abbrev].append(pair)
    return {"status": status, "spread": spread, "spread_source": source, "leaders": leaders}


def espn_series(
    sport: str,
    name: str,
    team: str,
    opponent: str,
    athlete_id: str,
    payload: dict,
    floor_date: str,
    position: str = "",
) -> PlayerSeries | None:
    """Current-season regular-season stats per game, most recent first. Games with 0 minutes are dropped."""
    if not isinstance(payload, dict):
        return None
    names = [str(item) for item in (payload.get("names") or [])]
    meta = payload.get("events") or {}
    regular = next(
        (
            item
            for item in payload.get("seasonTypes") or []
            if "regular season" in str(item.get("displayName") or "").lower() and "post" not in str(item.get("displayName") or "").lower()
        ),
        None,
    )
    if regular is None or not names:
        return None
    rows: dict[str, dict[str, str]] = {}
    for category in regular.get("categories") or []:
        columns = [str(item) for item in (category.get("names") or names)]
        for event in category.get("events") or []:
            event_id = str(event.get("eventId") or "")
            if not event_id:
                continue
            row = rows.setdefault(event_id, {})
            for column, raw in zip(columns, event.get("stats") or []):
                row[column] = raw
    dated: list[tuple[str, dict[str, str]]] = []
    for event_id, row in rows.items():
        when = str((meta.get(event_id) or {}).get("gameDate") or "")[:10]
        if when and when < floor_date:
            continue
        if "minutes" in row:
            minutes = _number(row["minutes"])
            if minutes is None or minutes <= 0:
                continue
        dated.append((when, row))
    dated.sort(key=lambda pair: pair[0], reverse=True)
    if not dated:
        return None
    stats: dict[str, list[float]] = {}
    for key, columns in ESPN_COLUMNS.get(sport, {}).items():
        column = next((item for item in columns if all(item in row for _when, row in dated)), None)
        if column is None:
            continue
        stats[key] = [_number(row[column]) or 0.0 for _when, row in dated]
    if not stats:
        return None
    return PlayerSeries(
        name=name,
        sport=sport,
        team=team,
        opponent=opponent,
        player_id=athlete_id,
        position=position,
        stats=stats,
        last_played=dated[0][0],
    )


def espn_flag_status(status: str) -> bool:
    text = status.lower()
    return any(word in text for word in OUT_WORDS)


def nba_defense_tables() -> dict[str, dict[str, DefenseRank]]:
    """Only points allowed is available league-wide for the NBA from ESPN standings."""
    from src.api.espn_client import fetch_standings

    table = fetch_standings("NBA")
    values = {abbrev: team.ga_per_game for abbrev, team in table.items() if team.ga_per_game and team.games_played}
    return {"POINTS": rank_table(values, verb="allows", noun="points")}


_nfl_cache: dict = {"at": 0.0, "table": {}}


def nfl_defense_tables(progress: Progress | None = None) -> dict[str, dict[str, DefenseRank]]:
    """Opponent passing, rushing and receiving per game from ESPN's team statistics (32 cached requests)."""
    if _nfl_cache["table"] and time.time() - _nfl_cache["at"] < TABLE_TTL:
        return _nfl_cache["table"]
    path = ESPN_PATH["NFL"]
    teams = get_json(ESPN_TEAMS.format(path=path), ttl_seconds=TABLE_TTL)
    entries = (((teams.get("sports") or [{}])[0].get("leagues") or [{}])[0].get("teams")) or []
    ids = [(str((item.get("team") or {}).get("id")), str((item.get("team") or {}).get("abbreviation"))) for item in entries]
    values: dict[str, dict[str, float]] = {key: {} for key in NFL_DEFENSE}

    def load(pair: tuple[str, str]) -> tuple[str, dict | None]:
        team_id, abbrev = pair
        try:
            return abbrev, get_json(ESPN_TEAM_STATS.format(path=path, team_id=team_id), ttl_seconds=TABLE_TTL)
        except Exception as exc:
            logger.debug("NFL team stats failed for %s: %s", abbrev, exc)
            return abbrev, None

    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for future in as_completed([pool.submit(load, pair) for pair in ids]):
            abbrev, payload = future.result()
            done += 1
            if progress is not None:
                progress(done / max(len(ids), 1), "Loading NFL defense table")
            if not payload:
                continue
            opponent = (payload.get("results") or {}).get("opponent") or []
            for key, (category, stat, _noun) in NFL_DEFENSE.items():
                for block in opponent:
                    if block.get("name") != category:
                        continue
                    for item in block.get("stats") or []:
                        if item.get("name") == stat and item.get("perGameValue") is not None:
                            values[key][abbrev] = float(item["perGameValue"])
    tables = {
        key: rank_table(per_team, verb="allows", noun=NFL_DEFENSE[key][2])
        for key, per_team in values.items()
        if len(per_team) >= 20
    }
    if tables:
        _nfl_cache.update({"at": time.time(), "table": tables})
    return tables


# --------------------------------------------------------------------------- orchestration


def _notify(progress: Progress | None, fraction: float, text: str) -> None:
    if progress is not None:
        try:
            progress(min(max(fraction, 0.0), 1.0), text)
        except Exception:  # a progress widget must never break a scan
            pass


def scan_game(sport: str, game, legs: Iterable[MarketLeg], *, progress: Progress | None = None) -> GameData:
    """Fetch and assemble one game's stat series. Safe to call from the UI thread; blocks until done."""
    sport = sport.upper()
    legs = list(legs)
    data = GameData(sport=sport, game_id=str(game.game_id), away=game.away.abbrev, home=game.home.abbrev)
    if sport == "NHL":
        _scan_nhl(data, game, legs, progress)
    elif sport in ESPN_KIND:
        _scan_espn(data, game, legs, progress)
    else:
        data.notes.append(f"{sport} is not supported.")
    data.partial = data.total > 0 and data.loaded < data.total
    return data


def _scan_nhl(data: GameData, game, legs: list[MarketLeg], progress: Progress | None) -> None:
    season = nhl_season_id(game.game_id, game.game_date)
    sides = ((game.away, game.home), (game.home, game.away))
    # Each task is a hashable key: ("rows", team abbrev, report) or ("defense",).
    tasks: list[tuple] = []
    team_ids: dict[str, int] = {}
    for team, _other in sides:
        if not team.team_id:
            data.notes.append(f"No NHL team id for {team.abbrev}; its stats were skipped.")
            continue
        team_ids[team.abbrev] = team.team_id
        for report in ("skater/summary", "skater/realtime", "goalie/summary"):
            tasks.append(("rows", team.abbrev, report))
    tasks.append(("defense",))
    data.total = len(tasks)
    results: dict[tuple, object] = {}
    failures = 0
    limited = False

    def run(task: tuple):
        if task[0] == "defense":
            return nhl_shot_tables(season)
        return _nhl_rows(task[2], team_ids[task[1]], season)

    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(run, task): task for task in tasks}
        for future in as_completed(futures):
            task = futures[future]
            done += 1
            try:
                results[task] = future.result()
                data.loaded += 1
            except Exception as exc:
                failures += 1
                limited = limited or _is_rate_limited(exc)
                logger.warning("NHL scan request failed: %s", type(exc).__name__)
            _notify(progress, done / max(len(tasks), 1), f"Loaded {data.loaded} of {data.total} NHL data sets")
    offered = offered_names(legs, "NHL", data.game_id)
    for team, other in sides:
        summary = results.get(("rows", team.abbrev, "skater/summary"))
        realtime = results.get(("rows", team.abbrev, "skater/realtime"))
        goalies = results.get(("rows", team.abbrev, "goalie/summary"))
        if summary:
            data.series.extend(nhl_skater_series(team.abbrev, other.abbrev, summary, realtime, offered))
            if realtime is None:
                data.skipped.append(f"{team.abbrev} hits and blocks (the NHL realtime report did not load)")
        if goalies:
            goalie = nhl_goalie_series(team.abbrev, other.abbrev, goalies)
            if goalie is not None:
                data.series.append(goalie)
    defense = results.get(("defense",))
    if defense:
        data.defense = defense
    else:
        data.notes.append("Defense ranks unavailable (league tables did not load), so no matchup traps were checked.")
    data.skipped.append("Hits and blocks defense rank unavailable (no per-game league data exists for them)")
    if failures:
        reason = "The NHL API is rate-limiting this computer" if limited else "Some NHL requests failed"
        data.notes.append(f"{reason}. Loaded {data.loaded} of {data.total} data sets; press Rescan in a minute to fill the rest from saved data.")


def _scan_espn(data: GameData, game, legs: list[MarketLeg], progress: Progress | None) -> None:
    sport = data.sport
    kind, league = ESPN_KIND[sport]
    path = ESPN_PATH[sport]
    sides = ((game.away, game.home), (game.home, game.away))
    floor = season_floor(game.game_date)
    context: dict = {"status": {}, "spread": None, "spread_source": "", "leaders": {}}
    rosters: dict[str, dict[str, dict]] = {}
    failures = 0
    limited = False
    first = [("summary", None)] + [("roster", team) for team, _other in sides if team.team_id]
    _notify(progress, 0.02, "Loading rosters and the game summary")

    def load_first(item):
        what, team = item
        if what == "summary":
            return what, None, get_json(SUMMARY.format(path=path), params={"event": game.game_id}, ttl_seconds=SUMMARY_TTL)
        return what, team, get_json(ESPN_ROSTER.format(path=path, team_id=team.team_id), ttl_seconds=TABLE_TTL)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for future in as_completed([pool.submit(load_first, item) for item in first]):
            try:
                what, team, payload = future.result()
            except Exception as exc:
                failures += 1
                limited = limited or _is_rate_limited(exc)
                continue
            if what == "summary":
                context = summary_context(payload)
            else:
                rosters[team.abbrev] = roster_index(payload)
    data.status = dict(context["status"])
    if context["spread"] is not None:
        data.spread = float(context["spread"])
        data.spread_source = str(context["spread_source"])
    offered = offered_names(legs, sport, data.game_id)
    candidates: list[tuple[str, str, str, str, str]] = []  # (name, id, team, opponent, position)
    for team, other in sides:
        roster = rosters.get(team.abbrev) or {}
        picked: list[tuple[str, dict]] = sorted(
            ((key, info) for key, info in roster.items() if offered.get(key)),
            key=lambda pair: (-offered[pair[0]], pair[0]),
        )
        if len(picked) < 6:
            for athlete_id, display in (context["leaders"].get(team.abbrev) or []):
                key = norm_name(display)
                if not any(key == existing for existing, _info in picked):
                    picked.append((key, roster.get(key) or {"id": athlete_id, "name": display, "position": ""}))
        for key, info in picked[:MAX_PER_TEAM]:
            status = data.status.get(key, "")
            if status and espn_flag_status(status):
                data.excluded.append(f"{info['name']} ({status})")
                continue
            candidates.append((info["name"], info["id"], team.abbrev, other.abbrev, info.get("position", "")))
    if not candidates:
        data.notes.append("No candidate players: no props are posted for this game and the team leaders did not load.")
    data.total = len(candidates)
    _notify(progress, 0.15, f"Loading game logs for {len(candidates)} players")

    def load_log(item):
        name, athlete_id, team, other, position = item
        payload = get_json(
            ESPN_GAMELOG.format(sport=kind, league=league, athlete_id=athlete_id),
            ttl_seconds=GAMELOG_TTL,
        )
        return item, payload

    done = 0
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for future in as_completed([pool.submit(load_log, item) for item in candidates]):
            done += 1
            try:
                (name, athlete_id, team, other, position), payload = future.result()
            except Exception as exc:
                failures += 1
                limited = limited or _is_rate_limited(exc)
                continue
            data.loaded += 1
            series = espn_series(sport, name, team, other, athlete_id, payload, floor, position)
            if series is not None:
                series.status = data.status.get(norm_name(name), "")
                data.series.append(series)
            _notify(progress, 0.15 + 0.7 * done / max(len(candidates), 1), f"Loaded {data.loaded} of {data.total} players")
    _notify(progress, 0.9, "Ranking the defenses")
    try:
        data.defense = nba_defense_tables() if sport == "NBA" else nfl_defense_tables(None)
    except Exception as exc:
        limited = limited or _is_rate_limited(exc)
        data.defense = {}
    if not data.defense:
        data.notes.append("Defense ranks unavailable (league tables did not load), so no matchup traps were checked.")
    if sport == "NBA":
        data.skipped.append("Rebounds, assists and threes defense rank unavailable (ESPN publishes no opponent splits for them); only points allowed is ranked")
    if data.spread is None:
        data.skipped.append("Game spread unavailable from ESPN; the board spread is used when a real one is posted")
    if failures:
        reason = "A host is rate-limiting this computer" if limited else "Some requests failed"
        data.notes.append(f"{reason}. Loaded {data.loaded} of {data.total} players; press Rescan in a minute to fill the rest from saved data.")
