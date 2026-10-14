"""Public ESPN scoreboard and standings for NFL and NBA. No API key."""

from __future__ import annotations

from datetime import datetime

from src.api.data_models import BoxScore, Game, SkaterLine, TeamProfile, TeamSide
from src.api.http_client import get_json

ESPN_PATH = {"NFL": "football/nfl", "NBA": "basketball/nba"}
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/{path}/scoreboard"
STANDINGS = "https://site.api.espn.com/apis/v2/sports/{path}/standings"
SUMMARY = "https://site.api.espn.com/apis/site/v2/sports/{path}/summary"


def _stat_map(stats: list[dict]) -> dict[str, float]:
    found: dict[str, float] = {}
    for row in stats or []:
        name = str(row.get("name") or "")
        value = row.get("value")
        if value is None:
            continue
        try:
            found[name] = float(value)
        except (TypeError, ValueError):
            continue
    return found


def parse_standings(payload: dict) -> dict[str, TeamProfile]:
    profiles: dict[str, TeamProfile] = {}
    groups = payload.get("children") or []
    if not groups and payload.get("standings"):
        groups = [payload]
    for group in groups:
        for entry in (group.get("standings") or {}).get("entries") or []:
            team = entry.get("team") or {}
            abbrev = team.get("abbreviation") or ""
            if not abbrev:
                continue
            stats = _stat_map(entry.get("stats") or [])
            wins = int(stats.get("wins") or 0)
            losses = int(stats.get("losses") or 0)
            ties = int(stats.get("ties") or 0)
            played = max(wins + losses + ties, 1)
            points_for = stats.get("pointsFor") or stats.get("avgPointsFor") or 0
            points_against = stats.get("pointsAgainst") or stats.get("avgPointsAgainst") or 0
            # Season totals are large; per-game averages from ESPN are already per game when small.
            per_game_for = points_for if points_for < played * 4 else points_for / played
            per_game_against = points_against if points_against < played * 4 else points_against / played
            streak = str(next((row.get("displayValue") for row in entry.get("stats") or [] if row.get("name") == "streak"), "") or "")
            profiles[abbrev] = TeamProfile(
                abbrev=abbrev,
                name=team.get("displayName") or abbrev,
                games_played=played,
                wins=wins,
                losses=losses,
                ot_losses=ties,
                gf_per_game=per_game_for or 22.0,
                ga_per_game=per_game_against or 22.0,
                l10_gp=min(played, 5),
                l10_wins=wins,
                l10_losses=losses,
                l10_gf_per_game=per_game_for or 22.0,
                l10_ga_per_game=per_game_against or 22.0,
                streak_code=streak[:1] if streak else "",
                streak_count=int("".join(ch for ch in streak if ch.isdigit()) or 0),
            )
    return profiles


def parse_scoreboard(payload: dict, slate_date: str) -> list[Game]:
    games: list[Game] = []
    for event in payload.get("events") or []:
        competition = (event.get("competitions") or [{}])[0]
        home = away = None
        for competitor in competition.get("competitors") or []:
            team = competitor.get("team") or {}
            side = TeamSide(
                team_id=int(team.get("id") or 0),
                abbrev=team.get("abbreviation") or "",
                name=team.get("displayName") or team.get("abbreviation") or "",
            )
            score_text = competitor.get("score")
            score = int(score_text) if str(score_text or "").isdigit() else None
            if competitor.get("homeAway") == "home":
                home = (side, score)
            else:
                away = (side, score)
        if not home or not away or not home[0].abbrev or not away[0].abbrev:
            continue
        status = ((event.get("status") or {}).get("type") or {})
        state = "FINAL" if status.get("completed") else "LIVE" if status.get("state") == "in" else "FUT"
        when = event.get("date") or ""
        game_date = when[:10] if len(when) >= 10 else slate_date
        games.append(
            Game(
                game_id=str(event.get("id") or ""),
                game_date=game_date,
                start_time_utc=when.replace("Z", "+00:00") if when.endswith("Z") else when,
                game_state=state,
                venue=((competition.get("venue") or {}).get("fullName") or ""),
                home=home[0],
                away=away[0],
                home_score=home[1],
                away_score=away[1],
            )
        )
    return games


def fetch_scoreboard(sport: str, slate_date: str) -> list[Game]:
    path = ESPN_PATH[sport]
    stamped = slate_date.replace("-", "")
    payload = get_json(SCOREBOARD.format(path=path), params={"dates": stamped}, ttl_seconds=300)
    games = [game for game in parse_scoreboard(payload, slate_date) if game.game_date == slate_date]
    if games:
        return games
    payload = get_json(SCOREBOARD.format(path=path), ttl_seconds=300)
    return parse_scoreboard(payload, slate_date)


def fetch_standings(sport: str) -> dict[str, TeamProfile]:
    path = ESPN_PATH[sport]
    payload = get_json(STANDINGS.format(path=path), ttl_seconds=900)
    return parse_standings(payload)


def fetch_summary_box(sport: str, event_id: str) -> BoxScore | None:
    path = ESPN_PATH[sport]
    payload = get_json(SUMMARY.format(path=path), params={"event": event_id}, ttl_seconds=120)
    header = payload.get("header") or {}
    competitions = header.get("competitions") or payload.get("competitions") or []
    competition = competitions[0] if competitions else {}
    status = ((competition.get("status") or header.get("status") or {}).get("type") or {})
    home = away = None
    for competitor in competition.get("competitors") or []:
        team = competitor.get("team") or {}
        abbrev = team.get("abbreviation") or ""
        score_text = competitor.get("score")
        try:
            score = int(score_text)
        except (TypeError, ValueError):
            score = 0
        pair = (abbrev, score)
        if competitor.get("homeAway") == "home":
            home = pair
        else:
            away = pair
    if not home or not away:
        return None
    state = "FINAL" if status.get("completed") else "LIVE" if status.get("state") == "in" else "FUT"
    skaters = _summary_players(payload)
    return BoxScore(
        game_id=str(event_id),
        game_state=state,
        home_abbrev=home[0],
        away_abbrev=away[0],
        home_score=home[1],
        away_score=away[1],
        skaters=skaters,
    )


def _summary_players(payload: dict) -> list[SkaterLine]:
    players: list[SkaterLine] = []
    box = payload.get("boxscore") or {}
    for group in box.get("players") or []:
        abbrev = (group.get("team") or {}).get("abbreviation") or ""
        for stat_group in group.get("statistics") or []:
            labels = [str(label).upper() for label in stat_group.get("labels") or []]
            name = str(stat_group.get("name") or "").lower()
            for athlete in stat_group.get("athletes") or []:
                person = athlete.get("athlete") or {}
                display = person.get("displayName") or ""
                if not display:
                    continue
                values = athlete.get("stats") or []
                stats: dict[str, float] = {}
                for label, raw in zip(labels, values):
                    try:
                        stats[label] = float(str(raw).split("/")[0])
                    except ValueError:
                        continue
                row = next((item for item in players if item.name == display and item.team_abbrev == abbrev), None)
                if row is None:
                    row = SkaterLine(player_id=int(person.get("id") or 0), name=display, team_abbrev=abbrev)
                    players.append(row)
                if "passing" in name and "YDS" in stats:
                    row.stats["PASS_YDS"] = stats["YDS"]
                if "rushing" in name and "YDS" in stats:
                    row.stats["RUSH_YDS"] = stats["YDS"]
                if "receiving" in name and "YDS" in stats:
                    row.stats["REC_YDS"] = stats["YDS"]
                if "TD" in stats and ("rushing" in name or "receiving" in name or "passing" in name):
                    row.stats["ANYTIME_TD"] = max(row.stats.get("ANYTIME_TD", 0), 1 if stats["TD"] > 0 else 0)
                if name in {"points", ""} and "PTS" in stats:
                    row.points = int(stats["PTS"])
                    row.stats["POINTS"] = stats["PTS"]
                if "REB" in stats:
                    row.stats["REBOUNDS"] = stats["REB"]
                if "AST" in stats:
                    row.stats["ASSISTS"] = stats["AST"]
                if "3PT" in stats or "3PM" in stats:
                    row.stats["THREES"] = stats.get("3PT", stats.get("3PM", 0))
    return players


def slate_date_from_iso(value: str) -> str:
    if not value:
        return datetime.utcnow().date().isoformat()
    return value[:10]
