"""Official NHL API wrapper with parsing that the tests can call directly."""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta

from config import NHL_STATS_BASE, NHL_WEB_BASE
from src.api.data_models import (
    BoxScore,
    Game,
    GoalieLine,
    GoalieProfile,
    PlayerProfile,
    SkaterLine,
    TeamProfile,
    TeamSide,
)
from src.api.http_client import get_json

logger = logging.getLogger(__name__)


def nhl_text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("default") or "")
    if value is None:
        return ""
    return str(value)


def _as_pct(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number > 1.5:
        return number / 100.0
    return number


def _window(values: list[int], size: int, fallback: float) -> tuple[float, int]:
    sample = values[:size]
    if not sample:
        return fallback, 0
    return sum(sample) / len(sample), len(sample)


def apply_recent_form(
    player: PlayerProfile,
    shots: list[int],
    goals: list[int],
    points: list[int],
) -> None:
    player.recent_shots = shots
    player.recent_goals = goals
    player.recent_points = points
    player.l5_sog, player.l5_games = _window(shots, 5, player.sog_per_game)
    player.l10_sog, player.l10_games = _window(shots, 10, player.sog_per_game)
    player.l5_goals, _ = _window(goals, 5, player.goals_per_game)
    player.l10_goals, _ = _window(goals, 10, player.goals_per_game)
    player.l5_points, _ = _window(points, 5, player.points_per_game)
    player.l10_points, _ = _window(points, 10, player.points_per_game)


def parse_schedule(payload: dict, slate_date: str) -> tuple[list[Game], dict[str, list[tuple[str, str]]]]:
    appearances: dict[str, list[tuple[str, str]]] = {}
    games: list[Game] = []
    for day in payload.get("gameWeek") or []:
        day_date = day.get("date") or ""
        bucket = appearances.setdefault(day_date, [])
        for raw in day.get("games") or []:
            away = raw.get("awayTeam") or {}
            home = raw.get("homeTeam") or {}
            away_abbrev = away.get("abbrev") or ""
            home_abbrev = home.get("abbrev") or ""
            if away_abbrev:
                bucket.append((away_abbrev, "away"))
            if home_abbrev:
                bucket.append((home_abbrev, "home"))
            if day_date != slate_date:
                continue
            games.append(
                Game(
                    game_id=str(raw.get("id")),
                    game_date=day_date,
                    start_time_utc=raw.get("startTimeUTC") or "",
                    game_state=raw.get("gameState") or "FUT",
                    venue=nhl_text((raw.get("venue") or {})) if isinstance(raw.get("venue"), dict) else str(raw.get("venue") or ""),
                    away=TeamSide(
                        team_id=int(away.get("id") or 0),
                        abbrev=away_abbrev,
                        place=nhl_text(away.get("placeName")),
                        common=nhl_text(away.get("commonName")),
                        name=f"{nhl_text(away.get('placeName'))} {nhl_text(away.get('commonName'))}".strip(),
                    ),
                    home=TeamSide(
                        team_id=int(home.get("id") or 0),
                        abbrev=home_abbrev,
                        place=nhl_text(home.get("placeName")),
                        common=nhl_text(home.get("commonName")),
                        name=f"{nhl_text(home.get('placeName'))} {nhl_text(home.get('commonName'))}".strip(),
                    ),
                    away_score=away.get("score"),
                    home_score=home.get("score"),
                )
            )
    return games, appearances


def parse_standings(payload: dict) -> dict[str, TeamProfile]:
    profiles: dict[str, TeamProfile] = {}
    for row in payload.get("standings") or []:
        abbrev = nhl_text(row.get("teamAbbrev"))
        if not abbrev:
            continue
        gp = int(row.get("gamesPlayed") or 0)
        gf = int(row.get("goalFor") or 0)
        ga = int(row.get("goalAgainst") or 0)
        l10_gp = int(row.get("l10GamesPlayed") or 0)
        l10_gf = float(row.get("l10GoalsFor") or 0)
        l10_ga = float(row.get("l10GoalsAgainst") or 0)
        profiles[abbrev] = TeamProfile(
            abbrev=abbrev,
            name=nhl_text(row.get("teamName")) or abbrev,
            games_played=gp,
            wins=int(row.get("wins") or 0),
            losses=int(row.get("losses") or 0),
            ot_losses=int(row.get("otLosses") or 0),
            points=int(row.get("points") or 0),
            goals_for=gf,
            goals_against=ga,
            gf_per_game=(gf / gp) if gp else 3.0,
            ga_per_game=(ga / gp) if gp else 3.0,
            l10_wins=int(row.get("l10Wins") or 0),
            l10_losses=int(row.get("l10Losses") or 0),
            l10_ot=int(row.get("l10OtLosses") or 0),
            l10_gp=l10_gp,
            l10_gf=l10_gf,
            l10_ga=l10_ga,
            l10_gf_per_game=(l10_gf / l10_gp) if l10_gp else (gf / gp if gp else 3.0),
            l10_ga_per_game=(l10_ga / l10_gp) if l10_gp else (ga / gp if gp else 3.0),
            home_wins=int(row.get("homeWins") or 0),
            home_losses=int(row.get("homeLosses") or 0),
            home_ot=int(row.get("homeOtLosses") or 0),
            road_wins=int(row.get("roadWins") or 0),
            road_losses=int(row.get("roadLosses") or 0),
            road_ot=int(row.get("roadOtLosses") or 0),
            streak_code=str(row.get("streakCode") or ""),
            streak_count=int(row.get("streakCount") or 0),
        )
    return profiles


def apply_team_summary(profiles: dict[str, TeamProfile], payload: dict) -> None:
    by_name = {team.name.casefold(): team for team in profiles.values()}
    for row in payload.get("data") or []:
        team = by_name.get(str(row.get("teamFullName") or "").casefold())
        if team is None:
            continue
        shots_for = row.get("shotsForPerGame")
        shots_against = row.get("shotsAgainstPerGame")
        if shots_for is not None:
            team.shots_for_per_game = float(shots_for)
        if shots_against is not None:
            team.shots_against_per_game = float(shots_against)
        pp = _as_pct(row.get("powerPlayPct"))
        pk = _as_pct(row.get("penaltyKillPct"))
        if pp is not None:
            team.power_play_pct = pp
        if pk is not None:
            team.penalty_kill_pct = pk


def apply_rest(
    profiles: dict[str, TeamProfile],
    appearances: dict[str, list[tuple[str, str]]],
    slate_date: str,
) -> None:
    slate = date.fromisoformat(slate_date)
    for abbrev, profile in profiles.items():
        prior: list[date] = []
        played_yesterday = False
        road_today = False
        for day_text, teams in appearances.items():
            try:
                day = date.fromisoformat(day_text)
            except ValueError:
                continue
            for team_abbrev, side in teams:
                if team_abbrev != abbrev:
                    continue
                if day == slate and side == "away":
                    road_today = True
                elif day < slate:
                    prior.append(day)
                    if day == slate - timedelta(days=1):
                        played_yesterday = True
        profile.back_to_back = played_yesterday
        profile.back_to_back_road = played_yesterday and road_today
        if prior:
            last = max(prior)
            profile.last_game_date = last.isoformat()
            profile.rest_days = max((slate - last).days - 1, 0)


def parse_club_stats(abbrev: str, payload: dict) -> tuple[list[PlayerProfile], list[GoalieProfile]]:
    players: list[PlayerProfile] = []
    for row in payload.get("skaters") or []:
        games = int(row.get("gamesPlayed") or 0)
        if games <= 0:
            continue
        shots = int(row.get("shots") or 0)
        goals = int(row.get("goals") or 0)
        assists = int(row.get("assists") or 0)
        points = int(row.get("points") or 0)
        players.append(
            PlayerProfile(
                player_id=int(row.get("playerId") or 0),
                name=f"{nhl_text(row.get('firstName'))} {nhl_text(row.get('lastName'))}".strip(),
                team_abbrev=abbrev,
                position=str(row.get("positionCode") or "F"),
                games_played=games,
                goals=goals,
                assists=assists,
                points=points,
                shots=shots,
                sog_per_game=shots / games,
                goals_per_game=goals / games,
                points_per_game=points / games,
                l5_sog=shots / games,
                l10_sog=shots / games,
                l5_goals=goals / games,
                l10_goals=goals / games,
                l5_points=points / games,
                l10_points=points / games,
            )
        )
    goalies: list[GoalieProfile] = []
    for row in payload.get("goalies") or []:
        starts = int(row.get("gamesStarted") or row.get("gamesPlayed") or 0)
        shots_against = int(row.get("shotsAgainst") or 0)
        saves = int(row.get("saves") or 0)
        goals_against = int(row.get("goalsAgainst") or 0)
        save_pct = _as_pct(row.get("savePercentage"))
        if save_pct is None and shots_against:
            save_pct = saves / shots_against
        goalies.append(
            GoalieProfile(
                player_id=int(row.get("playerId") or 0),
                name=f"{nhl_text(row.get('firstName'))} {nhl_text(row.get('lastName'))}".strip(),
                team_abbrev=abbrev,
                games_started=starts,
                save_pct=save_pct if save_pct is not None else 0.900,
                goals_against_average=float(row.get("goalsAgainstAverage") or 0.0),
                shots_against=shots_against,
                saves=saves,
                goals_against=goals_against,
            )
        )
    goalies.sort(key=lambda goalie: (goalie.games_started, goalie.save_pct), reverse=True)
    if goalies:
        goalies[0].projected_starter = True
    return players, goalies


def select_prop_players(players: list[PlayerProfile], limit: int = 2) -> list[PlayerProfile]:
    forwards = [player for player in players if player.position != "D"]
    pool = forwards or players
    pool = sorted(pool, key=lambda player: (player.points, player.shots), reverse=True)
    return pool[:limit]


def parse_game_log(payload: dict) -> tuple[list[int], list[int], list[int]]:
    rows = list(payload.get("gameLog") or [])
    rows.sort(key=lambda row: row.get("gameDate") or "", reverse=True)
    shots: list[int] = []
    goals: list[int] = []
    points: list[int] = []
    for row in rows[:10]:
        shots.append(int(row.get("shots") or 0))
        goals.append(int(row.get("goals") or 0))
        points.append(int(row.get("points") or 0))
    return shots, goals, points


def parse_boxscore(payload: dict) -> BoxScore:
    away = payload.get("awayTeam") or {}
    home = payload.get("homeTeam") or {}
    outcome = payload.get("gameOutcome") or {}
    period = payload.get("periodDescriptor") or {}
    last_period = str(outcome.get("lastPeriodType") or period.get("periodType") or "").upper()
    skaters: list[SkaterLine] = []
    goalies: list[GoalieLine] = []
    groups = payload.get("playerByGameStats") or {}
    for side_key, abbrev in (("awayTeam", away.get("abbrev") or ""), ("homeTeam", home.get("abbrev") or "")):
        side = groups.get(side_key) or {}
        for group in ("forwards", "defense"):
            for row in side.get(group) or []:
                skaters.append(
                    SkaterLine(
                        player_id=int(row.get("playerId") or 0),
                        name=nhl_text(row.get("name")),
                        team_abbrev=abbrev,
                        goals=int(row.get("goals") or 0),
                        assists=int(row.get("assists") or 0),
                        points=int(row.get("points") or 0),
                        sog=int(row.get("sog") or 0),
                    )
                )
        for row in side.get("goalies") or []:
            goalies.append(
                GoalieLine(
                    player_id=int(row.get("playerId") or 0),
                    name=nhl_text(row.get("name")),
                    team_abbrev=abbrev,
                    saves=int(row.get("saves") or 0),
                    goals_against=int(row.get("goalsAgainst") or 0),
                    shots_against=int(row.get("shotsAgainst") or 0),
                    starter=bool(row.get("starter")),
                )
            )
    return BoxScore(
        game_id=str(payload.get("id") or ""),
        game_state=str(payload.get("gameState") or ""),
        home_abbrev=home.get("abbrev") or "",
        away_abbrev=away.get("abbrev") or "",
        home_score=int(home.get("score") or 0),
        away_score=int(away.get("score") or 0),
        home_sog=int(home.get("sog") or 0),
        away_sog=int(away.get("sog") or 0),
        went_beyond_regulation=last_period in {"OT", "SO"},
        skaters=skaters,
        goalies=goalies,
    )


class NHLClient:
    def schedule(self, slate_date: str) -> tuple[list[Game], dict[str, list[tuple[str, str]]], int | None]:
        payload = get_json(f"{NHL_WEB_BASE}/schedule/{slate_date}", ttl_seconds=300)
        games, appearances = parse_schedule(payload, slate_date)
        season = None
        for day in payload.get("gameWeek") or []:
            for raw in day.get("games") or []:
                if raw.get("season"):
                    season = int(raw["season"])
                    break
            if season:
                break
        return games, appearances, season

    def standings(self) -> dict[str, TeamProfile]:
        payload = get_json(f"{NHL_WEB_BASE}/standings/now", ttl_seconds=900)
        return parse_standings(payload)

    def team_summary(self, season: int) -> dict:
        return get_json(
            f"{NHL_STATS_BASE}/team/summary",
            params={"cayenneExp": f"seasonId={season} and gameTypeId=2"},
            ttl_seconds=900,
        )

    def club_stats(self, abbrev: str, season: int) -> tuple[list[PlayerProfile], list[GoalieProfile]]:
        payload = get_json(f"{NHL_WEB_BASE}/club-stats/{abbrev}/{season}/2", ttl_seconds=900)
        return parse_club_stats(abbrev, payload)

    def game_log(self, player_id: int, season: int) -> tuple[list[int], list[int], list[int]]:
        payload = get_json(
            f"{NHL_WEB_BASE}/player/{player_id}/game-log/{season}/2",
            ttl_seconds=1800,
        )
        return parse_game_log(payload)

    def boxscore(self, game_id: str) -> BoxScore:
        payload = get_json(f"{NHL_WEB_BASE}/gamecenter/{game_id}/boxscore", ttl_seconds=120)
        return parse_boxscore(payload)

    def enrich_game_logs(self, players: list[PlayerProfile], season: int) -> None:
        def _load(player: PlayerProfile) -> tuple[int, tuple[list[int], list[int], list[int]] | None]:
            try:
                return player.player_id, self.game_log(player.player_id, season)
            except Exception as exc:
                logger.warning("Game log failed for %s: %s", player.player_id, exc)
                return player.player_id, None

        fetched: dict[int, tuple[list[int], list[int], list[int]]] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(_load, player) for player in players]
            for future in as_completed(futures):
                player_id, form = future.result()
                if form and form[0]:
                    fetched[player_id] = form
        for player in players:
            form = fetched.get(player.player_id)
            if form:
                apply_recent_form(player, *form)
