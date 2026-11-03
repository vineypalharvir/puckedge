from types import SimpleNamespace

from src.api.data_models import BoxScore, SkaterLine
from src.tracker.settlement import grade_leg, parse_custom_selection


def _box():
    return BoxScore(
        game_id="1",
        game_state="FINAL",
        home_abbrev="BOS",
        away_abbrev="VGK",
        home_score=3,
        away_score=2,
        skaters=[
            SkaterLine(player_id=1, name="R. Andersson", team_abbrev="BOS", assists=0, points=0),
            SkaterLine(player_id=2, name="H. Lindholm", team_abbrev="BOS", assists=1, points=1),
            SkaterLine(player_id=3, name="E. Lindholm", team_abbrev="BOS", assists=0, points=0),
        ],
    )


def _leg(market, selection, name, side="YES", line=None):
    return SimpleNamespace(
        market_type=market, selection=selection, target_entity=name, side=side, line=line, player_id=None, team_abbrev=None
    )


def test_parse_custom_selection():
    assert parse_custom_selection("Rasmus Andersson Under 0.5 Ast") == ("ASSISTS", "UNDER", 0.5)
    assert parse_custom_selection("Kawhi Leonard 25+ Points") == ("POINTS", "OVER", 24.5)
    assert parse_custom_selection("Josh Giddey Under 1.5 3PM") == ("THREES", "UNDER", 1.5)
    assert parse_custom_selection("nonsense") is None


def test_custom_under_assists_wins_and_loses():
    box = _box()
    won = grade_leg(_leg("CUSTOM", "Rasmus Andersson Under 0.5 Ast", "Rasmus Andersson"), box)
    assert won[0] == "WON"
    lost = grade_leg(_leg("CUSTOM", "Hampus Lindholm Under 0.5 Ast", "Hampus Lindholm"), box)
    assert lost[0] == "LOST"


def test_nhl_assists_market_uses_box_assists():
    box = _box()
    assert grade_leg(_leg("ASSISTS", "x", "R. Andersson", side="OVER", line=0.5), box)[0] == "LOST"
    assert grade_leg(_leg("ASSISTS", "x", "H. Lindholm", side="OVER", line=0.5), box)[0] == "WON"


def test_unreadable_custom_pick_stays_pending():
    assert grade_leg(_leg("CUSTOM", "something odd", "Nobody"), _box())[0] == "PENDING"
