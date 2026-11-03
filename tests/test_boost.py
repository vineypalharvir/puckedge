import pytest

from src.api.data_models import MarketLeg
from src.engine.boost import grade_boost, manual_leg, match_slip_line, parse_boost_price, split_bet_slip
from src.services.slate import load_board


def test_parse_american_and_decimal_prices():
    american, decimal_odds = parse_boost_price("+650")
    assert american == 650
    assert abs(decimal_odds - 7.5) < 1e-9
    american, decimal_odds = parse_boost_price("6.50")
    assert decimal_odds == 6.5
    assert american > 0


def test_pasted_boost_matches_board_legs_and_leans():
    board = load_board("2026-10-13", prefer_live=False)
    sample = next(leg for leg in board.legs if leg.selectable and leg.market_type == "GOALSCORER" and leg.side == "YES")
    total = next(
        leg
        for leg in board.legs
        if leg.selectable and leg.market_type == "TOTAL" and leg.side == "OVER" and leg.line == 6.5 and leg.game_id == sample.game_id
    )
    paste = f"{sample.selection}\n{sample.matchup} {total.selection}"
    grade = grade_boost("+800", paste, board.legs)
    assert len(grade.matched) == 2
    assert grade.model_prob is not None
    assert grade.lean in {"LEAN HIT", "LEAN MISS", "TOSS-UP"}
    assert "Model cash chance" in grade.summary


def test_unknown_line_does_not_invent_a_hit_chance():
    board = load_board("2026-10-13", prefer_live=False)
    grade = grade_boost("+400", "Some random mascot to dance\n", board.legs)
    assert grade.lean == "NO READ"
    assert grade.model_prob is None


def _leg(**kwargs) -> MarketLeg:
    base = dict(
        leg_id="x",
        game_id="g",
        matchup="SEA @ DEN",
        game_date="2026-10-15",
        start_time_utc="",
        market_type="MONEYLINE",
        market_key="g|ML",
        target_entity="Seattle Seahawks",
        selection="Seattle Seahawks ML",
        side="HOME",
        american_odds=-120,
        decimal_odds=1.83,
        implied_prob=0.55,
        fair_prob=0.52,
        model_prob=0.54,
        ev=0.01,
        bookmaker="draftkings",
        rationale="",
    )
    base.update(kwargs)
    return MarketLeg(**base)


def test_bet_slip_text_matches_moneyline_and_touchdowns():
    legs = [
        _leg(leg_id="sea", matchup="SEA @ DEN", target_entity="Seattle Seahawks", selection="Seattle Seahawks ML"),
        _leg(leg_id="edm", game_id="nhl", matchup="UTA @ EDM", market_key="nhl|ML", target_entity="Edmonton Oilers", selection="Edmonton Oilers ML", team_abbrev="EDM"),
        _leg(leg_id="wilson", market_type="ANYTIME_TD", market_key="g|TD|1", target_entity="Emanuel Wilson", selection="Emanuel Wilson anytime TD", side="YES", line=0.5, player_id=1),
        _leg(leg_id="dobbins", market_type="ANYTIME_TD", market_key="g|TD|2", target_entity="J.K. Dobbins", selection="J.K. Dobbins anytime TD", side="YES", line=0.5, player_id=2),
    ]
    slip = """
    EDM Oilers 1.95
    Money Line
    SAME GAME PARLAY 9.75
    Money Line - SEA Seahawks
    Emanuel Wilson to Score a TD
    Anytime TD Scorer
    J.K Dobbins to Score a TD
    """
    groups = split_bet_slip(slip)
    assert groups[0]["odds"] == "1.95"
    assert any(group["odds"] == "9.75" for group in groups)
    used: set[str] = set()
    found = []
    for group in groups:
        for line in group["lines"]:
            leg = match_slip_line(line, legs, used)
            if leg:
                used.add(leg.leg_id)
                found.append(leg.leg_id)
    assert "edm" in found
    assert "sea" in found
    assert "wilson" in found
    assert "dobbins" in found


def test_typed_leg_uses_the_odds_as_the_hit_chance():
    leg = manual_leg("Emanuel Wilson to Score a TD", "+150")
    assert leg.market_type == "ANYTIME_TD"
    assert abs(leg.model_prob - leg.implied_prob) < 1e-9
    assert abs(leg.model_prob - 0.4) < 1e-9


def test_bad_price_is_rejected():
    with pytest.raises(ValueError):
        parse_boost_price("0")
