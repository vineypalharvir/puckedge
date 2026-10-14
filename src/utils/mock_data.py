"""Deterministic offline slate used when the NHL feed is unreachable."""

from __future__ import annotations

import math
import random

from src.api.data_models import Game, GoalieProfile, PlayerProfile, SlateContext, TeamProfile, TeamSide
from src.api.nhl_client import apply_recent_form

# gp, wins, losses, ot, gf, ga, l10w, l10l, l10ot, l10gf, l10ga, sf, sa, pp, pk, streak
_TEAM_ROWS: dict[str, tuple] = {
    "NJD": ("New Jersey Devils", 8, 5, 2, 1, 28, 22, 5, 2, 1, 28, 22, 30.4, 28.1, 0.24, 0.82, "W", 2),
    "DET": ("Detroit Red Wings", 8, 4, 3, 1, 26, 25, 4, 3, 1, 26, 25, 29.2, 30.0, 0.19, 0.79, "L", 1),
    "TOR": ("Toronto Maple Leafs", 10, 6, 3, 1, 34, 30, 2, 6, 1, 18, 32, 31.0, 29.5, 0.27, 0.78, "L", 3),
    "BOS": ("Boston Bruins", 10, 6, 3, 1, 30, 24, 6, 2, 1, 24, 16, 28.4, 26.2, 0.18, 0.86, "W", 4),
    "EDM": ("Edmonton Oilers", 8, 5, 3, 0, 32, 24, 5, 3, 0, 32, 24, 32.6, 28.8, 0.29, 0.77, "W", 2),
    "COL": ("Colorado Avalanche", 8, 6, 2, 0, 33, 20, 6, 2, 0, 33, 20, 31.8, 27.1, 0.26, 0.84, "W", 3),
    "FLA": ("Florida Panthers", 8, 5, 2, 1, 27, 21, 4, 2, 1, 22, 18, 30.1, 27.6, 0.22, 0.83, "W", 1),
    "BUF": ("Buffalo Sabres", 8, 3, 5, 0, 22, 31, 2, 5, 0, 16, 28, 28.0, 33.8, 0.16, 0.74, "L", 2),
    "CAR": ("Carolina Hurricanes", 8, 6, 1, 1, 31, 18, 6, 1, 1, 31, 18, 33.2, 25.4, 0.21, 0.88, "W", 5),
    "PIT": ("Pittsburgh Penguins", 8, 4, 4, 0, 25, 26, 3, 4, 0, 20, 24, 29.5, 30.4, 0.23, 0.80, "L", 1),
    "DAL": ("Dallas Stars", 8, 5, 2, 1, 26, 19, 5, 2, 1, 26, 19, 28.8, 25.8, 0.20, 0.85, "W", 2),
    "MIN": ("Minnesota Wild", 8, 4, 3, 1, 24, 23, 3, 3, 1, 18, 22, 28.2, 29.6, 0.17, 0.81, "OT", 1),
    "VGK": ("Vegas Golden Knights", 8, 5, 2, 1, 29, 22, 4, 2, 1, 24, 20, 30.8, 28.4, 0.22, 0.82, "W", 1),
    "SJS": ("San Jose Sharks", 8, 2, 6, 0, 18, 34, 1, 6, 0, 12, 30, 26.4, 34.6, 0.14, 0.71, "L", 4),
    "NYR": ("New York Rangers", 8, 4, 3, 1, 24, 23, 4, 3, 1, 24, 23, 29.0, 28.7, 0.21, 0.83, "W", 1),
    "NYI": ("New York Islanders", 8, 3, 4, 1, 20, 25, 2, 4, 1, 16, 24, 27.5, 30.8, 0.15, 0.80, "L", 2),
}

_PLAYER_ROWS: list[tuple] = [
    (8478402, "Connor McDavid", "EDM", "C", 4.1, 0.55, 1.55),
    (8477934, "Leon Draisaitl", "EDM", "C", 3.4, 0.48, 1.25),
    (8479318, "Auston Matthews", "TOR", "C", 3.8, 0.62, 1.15),
    (8477939, "William Nylander", "TOR", "R", 2.9, 0.40, 0.95),
    (8477492, "Nathan MacKinnon", "COL", "C", 4.4, 0.50, 1.45),
    (8480801, "Mikko Rantanen", "COL", "R", 3.1, 0.42, 1.10),
    (8477956, "David Pastrnak", "BOS", "R", 3.9, 0.58, 1.20),
    (8473419, "Brad Marchand", "BOS", "L", 2.6, 0.32, 0.85),
    (8476453, "Nikita Kucherov", "TBL", "R", 3.3, 0.45, 1.30),
    (8475184, "Aleksander Barkov", "FLA", "C", 2.8, 0.36, 1.05),
    (8477404, "Sam Reinhart", "FLA", "C", 3.0, 0.50, 1.00),
    (8471675, "Sidney Crosby", "PIT", "C", 2.9, 0.34, 1.00),
    (8471214, "Evgeni Malkin", "PIT", "C", 2.7, 0.30, 0.85),
    (8478427, "Sebastian Aho", "CAR", "C", 2.8, 0.38, 0.95),
    (8478550, "Andrei Svechnikov", "CAR", "R", 3.2, 0.36, 0.90),
    (8470794, "Jack Hughes", "NJD", "C", 3.6, 0.42, 1.15),
    (8481559, "Nico Hischier", "NJD", "C", 2.5, 0.30, 0.85),
    (8475166, "Dylan Larkin", "DET", "C", 3.0, 0.34, 0.90),
    (8480839, "Lucas Raymond", "DET", "R", 2.6, 0.28, 0.75),
    (8480069, "Tage Thompson", "BUF", "C", 3.3, 0.40, 0.90),
    (8479420, "Rasmus Dahlin", "BUF", "D", 3.0, 0.18, 0.70),
    (8473994, "Jason Robertson", "DAL", "L", 3.1, 0.40, 1.05),
    (8479979, "Wyatt Johnston", "DAL", "C", 2.4, 0.32, 0.80),
    (8478864, "Kirill Kaprizov", "MIN", "L", 3.5, 0.48, 1.20),
    (8476853, "Matt Boldy", "MIN", "L", 2.5, 0.28, 0.75),
    (8478403, "Jack Eichel", "VGK", "C", 3.4, 0.40, 1.05),
    (8475913, "Mark Stone", "VGK", "R", 2.4, 0.30, 0.85),
    (8480066, "Macklin Celebrini", "SJS", "C", 3.2, 0.36, 0.95),
    (8482093, "Will Smith", "SJS", "C", 2.4, 0.22, 0.65),
    (8478550, "Artemi Panarin", "NYR", "L", 3.0, 0.34, 1.10),
    (8475184, "Mika Zibanejad", "NYR", "C", 2.8, 0.30, 0.80),
    (8477933, "Bo Horvat", "NYI", "C", 2.7, 0.32, 0.75),
    (8475151, "Mathew Barzal", "NYI", "C", 2.6, 0.24, 0.80),
]

# Unique ids. I duplicated a couple of real ids across teams above; fix by offsetting the second use.
# Aleksander Barkov id reused for Zibanejad and Svechnikov id reused for Panarin. Correct them here.
_ID_FIX = {
    ("Mika Zibanejad", "NYR"): 8476459,
    ("Artemi Panarin", "NYR"): 8478550,
    ("Andrei Svechnikov", "CAR"): 8480830,
}


def _poisson(rng: random.Random, lam: float, cap: int) -> int:
    limit = math.exp(-lam)
    count = 0
    product = 1.0
    while product > limit:
        count += 1
        product *= rng.random()
    return min(count - 1, cap)


def _team(abbrev: str, row: tuple, *, road_b2b: bool = False, home_b2b: bool = False) -> TeamProfile:
    name, gp, wins, losses, ot, gf, ga, l10w, l10l, l10ot, l10gf, l10ga, sf, sa, pp, pk, streak, streak_n = row
    l10_gp = l10w + l10l + l10ot
    return TeamProfile(
        abbrev=abbrev,
        name=name,
        games_played=gp,
        wins=wins,
        losses=losses,
        ot_losses=ot,
        points=wins * 2 + ot,
        goals_for=gf,
        goals_against=ga,
        gf_per_game=gf / gp,
        ga_per_game=ga / gp,
        l10_wins=l10w,
        l10_losses=l10l,
        l10_ot=l10ot,
        l10_gp=l10_gp,
        l10_gf=l10gf,
        l10_ga=l10ga,
        l10_gf_per_game=l10gf / l10_gp,
        l10_ga_per_game=l10ga / l10_gp,
        home_wins=max(wins // 2, 1),
        home_losses=max(losses // 2, 0),
        home_ot=0,
        road_wins=wins - max(wins // 2, 1),
        road_losses=losses - max(losses // 2, 0),
        road_ot=ot,
        streak_code=streak,
        streak_count=streak_n,
        shots_for_per_game=sf,
        shots_against_per_game=sa,
        power_play_pct=pp,
        penalty_kill_pct=pk,
        rest_days=0 if road_b2b or home_b2b else 2,
        back_to_back=road_b2b or home_b2b,
        back_to_back_road=road_b2b,
    )


def load_mock_context(slate_date: str) -> SlateContext:
    road_b2b = {"TOR"}
    home_b2b = {"BUF"}
    teams = {
        abbrev: _team(abbrev, row, road_b2b=abbrev in road_b2b, home_b2b=abbrev in home_b2b)
        for abbrev, row in _TEAM_ROWS.items()
    }
    rng = random.Random(13)
    players: list[PlayerProfile] = []
    for player_id, name, abbrev, position, sog, goals, points in _PLAYER_ROWS:
        if abbrev not in teams:
            continue
        player_id = _ID_FIX.get((name, abbrev), player_id)
        shots = [_poisson(rng, sog, 10) for _ in range(10)]
        goal_log = [_poisson(rng, goals, 4) for _ in range(10)]
        point_log = [max(goal_log[index], _poisson(rng, points, 6)) for index in range(10)]
        games = 10
        player = PlayerProfile(
            player_id=player_id,
            name=name,
            team_abbrev=abbrev,
            position=position,
            games_played=games,
            goals=sum(goal_log),
            assists=max(sum(point_log) - sum(goal_log), 0),
            points=sum(point_log),
            shots=sum(shots),
            sog_per_game=sum(shots) / games,
            goals_per_game=sum(goal_log) / games,
            points_per_game=sum(point_log) / games,
        )
        apply_recent_form(player, shots, goal_log, point_log)
        players.append(player)

    goalie_rows = [
        (1, "Jacob Markstrom", "NJD", 6, 0.912, 2.45, 180, 164, 16),
        (2, "Cam Talbot", "DET", 5, 0.901, 2.80, 160, 144, 16),
        (3, "Joseph Woll", "TOR", 4, 0.897, 2.95, 140, 126, 14),
        (4, "Jeremy Swayman", "BOS", 7, 0.918, 2.30, 210, 193, 17),
        (5, "Stuart Skinner", "EDM", 5, 0.899, 2.90, 155, 139, 16),
        (6, "Alexandar Georgiev", "COL", 6, 0.908, 2.55, 175, 159, 16),
        (7, "Sergei Bobrovsky", "FLA", 6, 0.914, 2.40, 170, 155, 15),
        (8, "Ukko-Pekka Luukkonen", "BUF", 6, 0.886, 3.35, 190, 168, 22),
        (9, "Frederik Andersen", "CAR", 6, 0.919, 2.20, 165, 152, 13),
        (10, "Tristan Jarry", "PIT", 5, 0.903, 2.75, 150, 135, 15),
        (11, "Jake Oettinger", "DAL", 6, 0.917, 2.25, 180, 165, 15),
        (12, "Filip Gustavsson", "MIN", 5, 0.905, 2.70, 155, 140, 15),
        (13, "Adin Hill", "VGK", 5, 0.910, 2.50, 150, 136, 14),
        (14, "Mackenzie Blackwood", "SJS", 6, 0.884, 3.55, 200, 177, 23),
        (15, "Igor Shesterkin", "NYR", 6, 0.915, 2.40, 175, 160, 15),
        (16, "Ilya Sorokin", "NYI", 6, 0.909, 2.60, 180, 164, 16),
    ]
    goalies = [
        GoalieProfile(
            player_id=player_id,
            name=name,
            team_abbrev=abbrev,
            games_started=starts,
            save_pct=save_pct,
            goals_against_average=gaa,
            shots_against=shots_against,
            saves=saves,
            goals_against=goals_against,
            projected_starter=True,
        )
        for player_id, name, abbrev, starts, save_pct, gaa, shots_against, saves, goals_against in goalie_rows
    ]
    matchups = [
        ("2026990001", "NJD", "DET", "T23:00:00Z"),
        ("2026990002", "TOR", "BOS", "T23:00:00Z"),
        ("2026990003", "EDM", "COL", "T01:00:00Z"),
        ("2026990004", "FLA", "BUF", "T23:30:00Z"),
        ("2026990005", "CAR", "PIT", "T23:00:00Z"),
        ("2026990006", "DAL", "MIN", "T00:00:00Z"),
        ("2026990007", "VGK", "SJS", "T02:00:00Z"),
        ("2026990008", "NYR", "NYI", "T23:00:00Z"),
    ]
    games = []
    for game_id, away, home, clock in matchups:
        away_team = teams[away]
        home_team = teams[home]
        games.append(
            Game(
                game_id=game_id,
                game_date=slate_date,
                start_time_utc=f"{slate_date}{clock}",
                game_state="FUT",
                venue="Offline mock rink",
                away=TeamSide(team_id=0, abbrev=away, name=away_team.name),
                home=TeamSide(team_id=0, abbrev=home, name=home_team.name),
            )
        )
    playing = {side for game in games for side in (game.home.abbrev, game.away.abbrev)}
    return SlateContext(
        slate_date=slate_date,
        source="mock",
        games=games,
        teams={abbrev: team for abbrev, team in teams.items() if abbrev in playing},
        players=[player for player in players if player.team_abbrev in playing],
        goalies=[goalie for goalie in goalies if goalie.team_abbrev in playing],
        notes=["Official NHL feed unavailable. Showing a full offline mock slate with synthetic Bet365 prices."],
    )
