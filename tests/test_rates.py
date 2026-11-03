from src.stats.catalog import markets_for
from src.stats.rates import rows_for_market, same_game_note, series_from_gamelog, series_from_overview, slip_chance


WILSON = {
    "gameLog": {
        "statistics": [
            {
                "names": ["rushingTouchdowns"],
                "events": [
                    {"eventId": "1", "stats": ["1"]},
                    {"eventId": "2", "stats": ["0"]},
                    {"eventId": "3", "stats": ["0"]},
                    {"eventId": "4", "stats": ["1"]},
                    {"eventId": "5", "stats": ["0"]},
                ],
            },
            {
                "names": ["receivingTouchdowns"],
                "events": [
                    {"eventId": "2", "stats": ["0"]},
                    {"eventId": "3", "stats": ["0"]},
                ],
            },
        ]
    }
}


def test_two_of_five_touchdowns_is_forty_percent():
    market = next(item for item in markets_for("NFL", "player") if item.key == "nfl_td")
    values = series_from_overview(WILSON, market.stat_keys)
    assert values is not None
    assert sum(1 for value in values if value >= 1) == 2
    row = next(item for item in rows_for_market(market, values) if item.side == "anytime")
    assert row.sample == "2 of 5 games"
    assert abs(row.probability - 0.4) < 1e-9


def test_zero_of_five_stays_small_and_says_zero():
    market = next(item for item in markets_for("NFL", "player") if item.key == "nfl_td")
    values = [0, 0, 0, 0, 0]
    row = next(item for item in rows_for_market(market, values) if item.side == "anytime")
    assert row.sample == "0 of 5 games"
    assert abs(row.probability - (1 / 7)) < 1e-9


def test_missing_stat_is_not_a_made_up_chance():
    market = next(item for item in markets_for("NHL", "skater") if item.key == "nhl_hits")
    row = rows_for_market(market, None)[0]
    assert row.sample == "no stat sample"
    assert row.probability is None


def test_last_five_is_the_front_of_the_log():
    market = next(item for item in markets_for("NBA", "player") if item.key == "nba_points")
    values = [30, 10, 10, 10, 10, 40]
    row = next(item for item in rows_for_market(market, values) if item.side == "milestone" and item.line == 20)
    assert row.l5_text == "1 of 5 games"
    assert row.season_text == "2 of 6 games"


def test_made_threes_and_rebounds_come_from_the_full_gamelog():
    market_reb = next(item for item in markets_for("NBA", "player") if item.key == "nba_rebounds")
    market_threes = next(item for item in markets_for("NBA", "player") if item.key == "nba_threes")
    payload = {
        "names": ["totalRebounds", "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "points"],
        "seasonTypes": [
            {
                "displayName": "Regular Season",
                "categories": [
                    {
                        "events": [
                            {"eventId": "1", "stats": ["12", "4-9", "30"]},
                            {"eventId": "2", "stats": ["8", "1-6", "18"]},
                            {"eventId": "3", "stats": ["11", "3-7", "22"]},
                            {"eventId": "4", "stats": ["9", "0-4", "14"]},
                            {"eventId": "5", "stats": ["14", "5-10", "28"]},
                        ]
                    }
                ],
            }
        ],
    }
    rebounds = series_from_gamelog(payload, market_reb.stat_keys)
    threes = series_from_gamelog(payload, market_threes.stat_keys)
    assert rebounds == [12, 8, 11, 9, 14]
    assert threes == [4, 1, 3, 0, 5]
    rebound_row = next(item for item in rows_for_market(market_reb, rebounds) if item.line == 10)
    three_row = next(item for item in rows_for_market(market_threes, threes) if item.line == 3 and item.side == "milestone")
    assert rebound_row.sample == "3 of 5 games"
    assert three_row.sample == "3 of 5 games"


def test_softer_defense_raises_the_chance_of_clearing_a_line():
    from src.stats.opponent import adjusted_probability, defense_factor, other_side

    market = next(item for item in markets_for("NBA", "player") if item.key == "nba_points")
    assert other_side("MIN @ TOR", "TOR") == "MIN"
    assert defense_factor(120, 110) > 1
    assert defense_factor(200, 110) == 1.15
    season = adjusted_probability(market, "milestone", 20, [22, 18, 19, 21, 10], 1.0)
    softer = adjusted_probability(market, "milestone", 20, [22, 18, 19, 21, 10], 1.15)
    assert season is not None and softer is not None
    assert softer > season


def test_custom_five_plus_blocks_uses_games_at_or_above_the_line():
    from src.stats.rates import parse_custom_line, rate_row

    market = next(item for item in markets_for("NBA", "player") if item.key == "nba_blocks")
    side, line, label = parse_custom_line("5+", market.label)
    assert side == "milestone"
    assert line == 5
    assert label == "5+ Blocks"
    row = rate_row(market, side, line, [6, 4, 5, 1, 0, 8], label)
    assert row.sample == "3 of 6 games"
    assert abs(row.probability - 0.5) < 1e-9


def test_play_in_games_are_not_the_regular_season():
    market = next(item for item in markets_for("NBA", "player") if item.key == "nba_points")
    payload = {
        "names": ["points"],
        "seasonTypes": [
            {"displayName": "2025-26 Play In Regular Season", "categories": [{"events": [{"eventId": "p", "stats": ["10"]}]}]},
            {
                "displayName": "2025-26 Regular Season",
                "categories": [{"events": [{"eventId": "a", "stats": ["30"]}, {"eventId": "b", "stats": ["12"]}]}],
            },
        ],
    }
    assert series_from_gamelog(payload, market.stat_keys) == [30, 12]


def test_slip_multiplies_and_flags_the_same_player():
    legs = [
        {"player": "Emanuel Wilson", "matchup": "SEA @ DEN", "probability": 0.4},
        {"player": "J.K. Dobbins", "matchup": "SEA @ DEN", "probability": 0.2},
    ]
    assert abs(slip_chance(legs) - 0.08) < 1e-9
    assert "Same game" in same_game_note(legs)
    assert same_game_note([{"player": "A", "matchup": "A @ B", "probability": 0.5}, {"player": "B", "matchup": "C @ D", "probability": 0.5}]) == ""
