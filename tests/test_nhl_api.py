from src.api.nhl_client import parse_boxscore, parse_schedule, parse_standings


def test_parse_schedule_and_standings_fixtures():
    schedule = {
        "gameWeek": [
            {
                "date": "2026-10-13",
                "games": [
                    {
                        "id": 2026020090,
                        "season": 20262027,
                        "startTimeUTC": "2026-10-13T22:00:00Z",
                        "gameState": "FUT",
                        "venue": {"default": "Little Caesars Arena"},
                        "awayTeam": {
                            "id": 1,
                            "abbrev": "NJD",
                            "placeName": {"default": "New Jersey"},
                            "commonName": {"default": "Devils"},
                        },
                        "homeTeam": {
                            "id": 17,
                            "abbrev": "DET",
                            "placeName": {"default": "Detroit"},
                            "commonName": {"default": "Red Wings"},
                        },
                    }
                ],
            },
            {
                "date": "2026-10-12",
                "games": [
                    {
                        "id": 1,
                        "awayTeam": {"id": 1, "abbrev": "NJD"},
                        "homeTeam": {"id": 2, "abbrev": "BUF"},
                    }
                ],
            },
        ]
    }
    games, appearances = parse_schedule(schedule, "2026-10-13")
    assert len(games) == 1
    assert games[0].away.name == "New Jersey Devils"
    assert games[0].home.abbrev == "DET"
    assert ("NJD", "away") in appearances["2026-10-12"]

    standings = parse_standings(
        {
            "standings": [
                {
                    "teamAbbrev": {"default": "CAR"},
                    "teamName": {"default": "Carolina Hurricanes"},
                    "gamesPlayed": 7,
                    "wins": 5,
                    "losses": 1,
                    "otLosses": 1,
                    "points": 11,
                    "goalFor": 27,
                    "goalAgainst": 17,
                    "l10GamesPlayed": 7,
                    "l10Wins": 5,
                    "l10Losses": 1,
                    "l10OtLosses": 1,
                    "l10GoalsFor": 27,
                    "l10GoalsAgainst": 17,
                    "streakCode": "W",
                    "streakCount": 5,
                }
            ]
        }
    )
    assert standings["CAR"].gf_per_game == 27 / 7
    assert standings["CAR"].streak_count == 5


def test_parse_boxscore_fixture():
    box = parse_boxscore(
        {
            "id": 2026020087,
            "gameState": "OFF",
            "gameOutcome": {"lastPeriodType": "REG"},
            "awayTeam": {"abbrev": "FLA", "score": 3, "sog": 23},
            "homeTeam": {"abbrev": "BUF", "score": 5, "sog": 32},
            "playerByGameStats": {
                "awayTeam": {
                    "forwards": [
                        {
                            "playerId": 8480801,
                            "name": {"default": "B. Tkachuk"},
                            "goals": 0,
                            "assists": 1,
                            "points": 1,
                            "sog": 3,
                        }
                    ],
                    "defense": [],
                    "goalies": [
                        {
                            "playerId": 8474593,
                            "name": {"default": "J. Markstrom"},
                            "saves": 9,
                            "goalsAgainst": 2,
                            "shotsAgainst": 11,
                            "starter": True,
                        }
                    ],
                },
                "homeTeam": {"forwards": [], "defense": [], "goalies": []},
            },
        }
    )
    assert box.is_final
    assert box.skaters[0].sog == 3
    assert box.goalies[0].saves == 9
    assert box.went_beyond_regulation is False
