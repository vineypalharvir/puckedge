import pytest

from src.engine.boost import grade_boost, parse_boost_price
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


def test_bad_price_is_rejected():
    with pytest.raises(ValueError):
        parse_boost_price("0")
