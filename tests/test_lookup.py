from src.engine.boost import is_context_line, is_player_line, split_bet_slip
from src.stats.lookup import leg_from_overview


WILSON = {
    "statistics": {
        "names": ["rushingTouchdowns", "receivingTouchdowns"],
        "splits": [{"displayName": "Regular Season", "stats": ["2", "1"]}],
    },
    "gameLog": {
        "statistics": [
            {
                "names": ["rushingTouchdowns"],
                "events": [
                    {"eventId": "1", "stats": ["1"]},
                    {"eventId": "2", "stats": ["1"]},
                    {"eventId": "3", "stats": ["0"]},
                    {"eventId": "4", "stats": ["0"]},
                    {"eventId": "5", "stats": ["0"]},
                ],
            },
            {
                "names": ["receivingTouchdowns"],
                "events": [
                    {"eventId": "3", "stats": ["1"]},
                ],
            },
        ]
    },
    "nextGame": {"league": {"events": [{"shortName": "SEA @ DEN"}]}},
}


def test_touchdown_chance_is_games_with_a_score():
    leg = leg_from_overview("Emanuel Wilson", "nfl", "Emanuel Wilson to Score a TD", WILSON)
    assert leg is not None
    assert leg.market_type == "ANYTIME_TD"
    assert abs(leg.model_prob - 0.6) < 1e-9
    assert "3 of 5" in leg.rationale
    assert "2 rush TD" in leg.rationale
    assert leg.matchup == "SEA @ DEN"
    assert leg.tags == ["NFL"]
    assert leg.decimal_odds == 1 / 0.6


def test_zero_scores_stay_a_small_chance():
    overview = {
        "gameLog": {
            "statistics": [
                {
                    "names": ["goals"],
                    "events": [
                        {"eventId": "1", "stats": ["0"]},
                        {"eventId": "2", "stats": ["0"]},
                        {"eventId": "3", "stats": ["0"]},
                    ],
                }
            ]
        }
    }
    leg = leg_from_overview("Depth Forward", "nhl", "Depth Forward to score", overview)
    assert leg is not None
    assert leg.market_type == "GOALSCORER"
    assert abs(leg.model_prob - (1 / 5)) < 1e-9


def test_slip_keeps_bets_and_drops_matchup_headers():
    groups = split_bet_slip(
        """
        EDM Oilers 1.95
        Money Line
        UTA Mammoth
        SAME GAME PARLAY 9.75
        Money Line - SEA Seahawks
        Emanuel Wilson to Score a TD
        SEA Seahawks
        DEN Broncos
        """
    )
    assert groups[0]["odds"] == "1.95"
    assert "EDM Oilers money line" in groups[0]["lines"]
    bets = [line for line in groups[1]["lines"] if not is_context_line(line)]
    assert bets == ["Money Line - SEA Seahawks", "Emanuel Wilson to Score a TD"]
    assert is_player_line("Emanuel Wilson to Score a TD")
    assert not is_player_line("Money Line - SEA Seahawks")
