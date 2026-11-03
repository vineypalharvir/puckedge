from src.engine.generator import PROFILES, _candidate_pool, generate_parlays
from src.limits import capped_stake, load_max_stake, save_max_stake
from src.services.slate import load_board
from src.utils.mock_data import load_mock_context  # noqa: F401  (mock slate is used by load_board offline)


def test_limit_is_saved_per_account(tmp_path):
    path = tmp_path / "limits.json"
    assert load_max_stake("Harvir", path) is None
    save_max_stake("Harvir", 12.5, path)
    save_max_stake("friend", 40, path)
    assert load_max_stake("harvir", path) == 12.5
    assert load_max_stake("FRIEND", path) == 40.0


def test_limit_rejects_zero_and_bad_values(tmp_path):
    path = tmp_path / "limits.json"
    for bad in (0, -5, float("nan"), "abc"):
        try:
            save_max_stake("a", bad, path)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should be rejected")
    assert load_max_stake("a", path) is None


def test_capped_stake():
    assert capped_stake(50, 20) == 20
    assert capped_stake(10, 20) == 10
    assert capped_stake(50, None) == 50
    assert capped_stake(-5, 20) == 0.0


def test_tickets_never_exceed_the_limit():
    board = load_board("2026-10-22", prefer_live=False)
    tickets = generate_parlays(board.legs, "Conservative", bankroll=1000, stake=50, max_stake=10)
    assert tickets
    for ticket in tickets:
        assert ticket.stake <= 10
        assert ticket.recommended_stake <= 10
        assert ticket.net_profit_if_win >= 0


def test_balanced_is_stricter_than_before_and_skips_estimated_prices():
    profile = PROFILES["Balanced"]
    assert (profile.min_legs, profile.max_legs) == (2, 5)
    assert (profile.min_american, profile.max_american) == (110, 250)
    assert profile.target_prob == (0.40, 0.45)
    assert profile.min_leg_prob == 0.62
    board = load_board("2026-10-22", prefer_live=False)
    mixed = [leg.model_copy(update={"bookmaker": "draftkings"}) for leg in board.legs[:40]] + board.legs[40:]
    pool, _relaxed = _candidate_pool(mixed, profile)
    assert pool
    assert all(leg.bookmaker != "synthetic-bet365" for leg in pool)
    # With no real line anywhere (offline), estimated legs are kept so the tier still builds.
    offline_pool, _ = _candidate_pool(board.legs, profile)
    assert offline_pool
