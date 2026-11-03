from src.stats.search import espn_profile, nhl_profile

SKATER = {
    "position": "C",
    "featuredStats": {
        "season": 20262027,
        "regularSeason": {
            "subSeason": {"gamesPlayed": 10, "goals": 7, "assists": 6, "points": 13, "plusMinus": -4, "pim": 2, "shots": 31, "powerPlayGoals": 5},
            "career": {"gamesPlayed": 680, "goals": 182, "assists": 328, "points": 510, "pim": 156},
        },
    },
    "careerTotals": {
        "regularSeason": {"gamesPlayed": 680, "goals": 182, "assists": 328, "points": 510, "plusMinus": -12, "pim": 156, "shots": 1282, "powerPlayGoals": 59, "avgToi": "17:52"}
    },
    "seasonTotals": [
        {"season": 20252026, "gameTypeId": 2, "leagueAbbrev": "NHL", "gamesPlayed": 82, "avgToi": "19:00"},
        {"season": 20262027, "gameTypeId": 2, "leagueAbbrev": "NHL", "gamesPlayed": 10, "avgToi": "18:17"},
    ],
    "last5Games": [
        {"gameDate": "2026-10-19", "homeRoadFlag": "H", "opponentAbbrev": "PIT", "goals": 1, "assists": 1, "points": 2, "plusMinus": 1, "pim": 0, "powerPlayGoals": 1, "shorthandedGoals": 0, "shots": 2, "shifts": 18, "toi": "17:41"},
        {"gameDate": "2026-10-15", "homeRoadFlag": "R", "opponentAbbrev": "EDM", "goals": 0, "assists": 2, "points": 2, "plusMinus": 1, "pim": 0, "powerPlayGoals": 0, "shorthandedGoals": 0, "shots": 2, "shifts": 22, "toi": "19:15"},
    ],
}


def test_nhl_profile_skater_totals_career_and_games():
    card = nhl_profile(SKATER)
    assert card["season"] == "2026-27 Season"
    season = dict(card["season_tiles"])
    assert season == {"GP": "10", "G": "7", "A": "6", "P": "13", "+/-": "-4", "PIM": "2", "SOG": "31", "PPG": "5", "TOI/GP": "18:17"}
    assert dict(card["per_game"]) == {"G/GP": "0.70", "A/GP": "0.60", "P/GP": "1.30", "SOG/GP": "3.10"}
    career = dict(card["career"])
    assert career["GP"] == "680" and career["P"] == "510" and career["SOG"] == "1,282" and career["TOI/GP"] == "17:52"
    assert card["games"]["columns"] == ["Date", "Matchup", "G", "A", "P", "+/-", "PIM", "PPG", "SHG", "S", "SHIFT", "TOI"]
    assert card["games"]["rows"][0] == ["Oct 19", "vs PIT", "1", "1", "2", "+1", "0", "1", "0", "2", "18", "17:41"]
    assert card["games"]["rows"][1][1] == "@ EDM"


def test_nhl_profile_weights_toi_across_traded_stints():
    landing = {
        "featuredStats": {"season": 20262027, "regularSeason": {"subSeason": {"gamesPlayed": 4, "goals": 1}}},
        "seasonTotals": [
            {"season": 20262027, "gameTypeId": 2, "leagueAbbrev": "NHL", "gamesPlayed": 1, "avgToi": "20:00"},
            {"season": 20262027, "gameTypeId": 2, "leagueAbbrev": "NHL", "gamesPlayed": 3, "avgToi": "16:00"},
        ],
    }
    assert dict(nhl_profile(landing)["season_tiles"])["TOI/GP"] == "17:00"


def test_nhl_profile_omits_missing_fields():
    card = nhl_profile({"featuredStats": {"season": 20262027, "regularSeason": {"subSeason": {"gamesPlayed": 3, "goals": 2}}}})
    assert card["season_tiles"] == [("GP", "3"), ("G", "2")]
    assert card["per_game"] == [("G/GP", "0.67")]
    assert card["career"] == []
    assert card["games"] == {}


def test_nhl_profile_goalie_uses_goalie_stats():
    landing = {
        "position": "G",
        "featuredStats": {"season": 20262027, "regularSeason": {"subSeason": {"gamesPlayed": 8, "wins": 5, "losses": 2, "otLosses": 0, "savePctg": 0.87931, "goalsAgainstAvg": 2.892783, "shutouts": 1}}},
        "last5Games": [{"gameDate": "2026-10-17", "homeRoadFlag": "H", "opponentAbbrev": "EDM", "decision": "L", "shotsAgainst": 17, "goalsAgainst": 5, "savePctg": 0.705882, "toi": "33:48"}],
    }
    card = nhl_profile(landing)
    assert dict(card["season_tiles"]) == {"GP": "8", "W": "5", "L": "2", "OTL": "0", "SV%": ".879", "GAA": "2.89", "SO": "1"}
    assert card["per_game"] == []
    assert card["games"]["rows"] == [["Oct 17", "vs EDM", "L", "17", "5", ".706", "33:48"]]


GAMELOG = {
    "names": ["minutes", "fieldGoalsMade-fieldGoalsAttempted", "totalRebounds", "assists", "points"],
    "events": {
        "1": {"gameDate": "2026-10-18T00:00:00.000+00:00", "atVs": "@", "opponent": {"abbreviation": "NY"}, "gameResult": "W", "score": "106-100"},
        "2": {"gameDate": "2026-10-20T00:00:00.000+00:00", "atVs": "vs", "opponent": {"abbreviation": "BOS"}, "gameResult": "L", "score": "99-101"},
    },
    "seasonTypes": [
        {
            "displayName": "2026-27 Regular Season",
            "categories": [
                {
                    "events": [
                        {"eventId": "1", "stats": ["34", "5-13", "9", "7", "14"]},
                        {"eventId": "2", "stats": ["30", "8-15", "5", "3", "20"]},
                    ]
                }
            ],
        },
        {"displayName": "2025-26 Regular Season", "categories": [{"events": [{"eventId": "9", "stats": ["30", "1-2", "1", "1", "2"]}]}]},
    ],
}


def test_espn_profile_totals_per_game_and_recent_games():
    card = espn_profile(GAMELOG)
    assert card["season"] == "2026-27 Regular Season"
    assert card["season_tiles"] == [("GP", "2"), ("PTS", "34"), ("REB", "14"), ("AST", "10")]
    assert dict(card["per_game"]) == {"MIN/G": "32.0", "PTS/G": "17.0", "REB/G": "7.0", "AST/G": "5.0"}
    assert card["career"] == []
    assert card["games"]["columns"] == ["Date", "Matchup", "Result", "MIN", "PTS", "REB", "AST"]
    assert card["games"]["rows"][0] == ["Oct 20", "vs BOS", "L 99-101", "30", "20", "5", "3"]
    assert card["games"]["rows"][1][1] == "@ NY"
    assert len(card["past"]) == 1


def test_espn_profile_empty_gamelog():
    assert espn_profile({}) == {}
