from src.engine.devig import (
    american_to_decimal,
    american_to_implied,
    decimal_to_american,
    expected_value,
    multiplicative_devig,
    poisson_sf_over,
    power_devig,
)
from src.utils.kelly import kelly_fraction


def test_american_implied_probabilities():
    assert abs(american_to_implied(100) - 0.5) < 1e-9
    assert abs(american_to_implied(-150) - (150 / 250)) < 1e-9
    assert abs(american_to_implied(150) - (100 / 250)) < 1e-9


def test_decimal_roundtrip():
    assert abs(american_to_decimal(-110) - (1 + 100 / 110)) < 1e-9
    assert decimal_to_american(american_to_decimal(160)) == 160
    assert decimal_to_american(american_to_decimal(-140)) == -140


def test_devig_removes_the_hold():
    implied = [american_to_implied(-110), american_to_implied(-110)]
    fair = multiplicative_devig(implied)
    assert abs(sum(fair) - 1.0) < 1e-9
    assert abs(fair[0] - 0.5) < 1e-9
    powered = power_devig(implied)
    assert abs(sum(powered) - 1.0) < 1e-9


def test_expected_value_matches_the_identity_when_there_is_no_push():
    decimal_odds = american_to_decimal(150)
    assert abs(expected_value(0.45, decimal_odds) - (0.45 * decimal_odds - 1)) < 1e-9


def test_save_totals_use_the_full_distribution():
    over, _push = poisson_sf_over(27.5, 28.5)
    assert 0.15 < over < 0.6


def test_quarter_kelly():
    # Even money, 60% win rate: full Kelly is 20%, quarter Kelly is 5%.
    fraction = kelly_fraction(2.0, 0.60, 0.25)
    assert abs(fraction - 0.05) < 1e-9
