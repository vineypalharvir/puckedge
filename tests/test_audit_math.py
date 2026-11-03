"""Odds conversion, money rounding, Kelly guardrails, push/void settlement, and the ledger schema."""

import sqlite3
from datetime import timezone
from types import SimpleNamespace

import pytest

from src.api.data_models import BoxScore, BuiltParlay, MarketLeg, SkaterLine
from src.engine.devig import (
    american_to_decimal,
    american_to_implied,
    combine_decimal,
    decimal_to_american,
    format_american,
    is_valid_american,
)
from src.engine.generator import PROFILES
from src.tracker.database import open_session
from src.tracker.models import ParlayLeg
from src.tracker.repository import get_balance, list_parlays, route_leg, save_parlay, set_balance, settle_pending
from src.tracker.settlement import grade_leg, rollup_ticket
from src.utils.kelly import (
    EIGHTH_KELLY,
    NEEDS_BANKROLL,
    NEEDS_WAGER,
    QUARTER_KELLY,
    TIER_KELLY_FRACTIONS,
    exposure_message,
    exposure_pct,
    exposure_text,
    kelly_fraction,
    sizing_warning,
)
from src.utils.money import format_money, payout_amount, profit_amount, to_cents


# ---------- American <-> decimal ----------


@pytest.mark.parametrize(
    "american, expected",
    [(100, 2.0), (150, 2.5), (250, 3.5), (-100, 2.0), (-200, 1.5), (-110, 1 + 100 / 110), (10000, 101.0)],
)
def test_american_to_decimal_matches_the_spec(american, expected):
    assert american_to_decimal(american) == pytest.approx(expected)


@pytest.mark.parametrize("bad", [0, 50, -50, 99, -99, None, float("nan"), float("inf"), True, "abc"])
def test_american_to_decimal_rejects_prices_no_book_posts(bad):
    assert not is_valid_american(bad)
    with pytest.raises(ValueError):
        american_to_decimal(bad)
    with pytest.raises(ValueError):
        american_to_implied(bad)


@pytest.mark.parametrize(
    "decimal_odds, expected",
    [(2.0, 100), (2.5, 150), (3.5, 250), (1.5, -200), (1 + 100 / 110, -110), (2.51, 151), (11.0, 1000)],
)
def test_decimal_to_american_matches_the_spec(decimal_odds, expected):
    assert decimal_to_american(decimal_odds) == expected


@pytest.mark.parametrize("bad", [1.0, 0.5, 0, -3, None, float("nan"), float("inf"), "x"])
def test_decimal_to_american_guards_non_paying_prices(bad):
    assert decimal_to_american(bad) == -10000


def test_round_trip_for_every_common_price():
    for american in list(range(100, 1000, 25)) + list(range(-1000, -100, 25)) + [100]:
        assert decimal_to_american(american_to_decimal(american)) == american


def test_format_american_never_raises():
    assert format_american(150) == "+150"
    assert format_american(-110) == "-110"
    assert format_american(None) == "—"
    assert format_american(float("nan")) == "—"


def test_combine_decimal_is_the_product_and_a_push_is_one():
    assert combine_decimal([2.0, 3.0, 1.5]) == pytest.approx(9.0)
    assert combine_decimal([2.5, 1.0]) == combine_decimal([2.5])
    assert combine_decimal([]) == 1.0
    # Float multiplication drifts here; the decimal product does not.
    assert combine_decimal([1.1, 1.1, 1.1]) == 1.331


# ---------- money ----------


def test_money_rounds_half_up_without_float_drift():
    assert payout_amount(10, 1.0525) == 10.53
    assert to_cents(2.675) == 2.68
    assert format_money(2.675) == "$2.68"
    assert f"{2.675:.2f}" == "2.67"
    assert format_money(-1234.5) == "-$1,234.50"
    assert profit_amount(25, 4.0) == 75.0
    assert payout_amount(25, 4.0) - 25 == profit_amount(25, 4.0)


def test_money_handles_garbage_without_raising():
    assert to_cents(float("nan")) == 0.0
    assert to_cents(None) == 0.0
    assert payout_amount(None, 2.0) == 0.0


# ---------- Kelly and exposure ----------


def test_tier_fractions_are_explicit_and_the_profiles_use_them():
    assert QUARTER_KELLY == 0.25 and EIGHTH_KELLY == 0.125
    assert TIER_KELLY_FRACTIONS["Conservative"] == QUARTER_KELLY
    assert TIER_KELLY_FRACTIONS["Balanced"] == EIGHTH_KELLY
    for tier, profile in PROFILES.items():
        assert profile.kelly_risk == TIER_KELLY_FRACTIONS[tier]


def test_kelly_formula_and_non_negative_stake():
    # b = 1, p = 0.6, q = 0.4 -> f* = (0.6 - 0.4) / 1 = 0.2
    assert kelly_fraction(2.0, 0.6, 1.0) == pytest.approx(0.2)
    assert kelly_fraction(2.0, 0.6, QUARTER_KELLY) == pytest.approx(0.05)
    assert kelly_fraction(2.0, 0.6, EIGHTH_KELLY) == pytest.approx(0.025)
    assert kelly_fraction(2.0, 0.4, 1.0) == 0.0  # negative edge stakes nothing
    assert kelly_fraction(2.0, 0.5, 1.0) == 0.0
    for bad in (float("nan"), None, -1):
        assert kelly_fraction(bad, 0.6, 0.25) == 0.0
        assert kelly_fraction(2.0, bad, 0.25) == 0.0
    assert kelly_fraction(1.0, 0.9, 0.25) == 0.0
    assert kelly_fraction(2.0, 1.7, 1.0) == pytest.approx(1.0)  # probability is clamped to 1


def test_exposure_is_never_negative_or_nonsense():
    assert exposure_pct(50, 1000) == pytest.approx(0.05)
    assert exposure_text(50, 1000) == "5.0%"
    assert exposure_message(50, 1000) is None
    for wager, bankroll in [(500, -100), (500, 0), (-5, 1000), (0, 1000), (float("nan"), 1000), (500, float("nan"))]:
        assert exposure_pct(wager, bankroll) == 0.0
        assert exposure_text(wager, bankroll) == "—"
    assert exposure_message(500, -100) == NEEDS_BANKROLL
    assert exposure_message(0, 1000) == NEEDS_WAGER
    assert sizing_warning("Balanced", 500, -100, 10.0) == NEEDS_BANKROLL
    assert sizing_warning("Balanced", 0, 1000, 10.0) is None
    assert "-" not in (sizing_warning("Balanced", 90, 1000, 10.0) or "")


# ---------- push / void rules ----------


def _leg(status, american):
    return SimpleNamespace(leg_status=status, american_odds=american)


def test_rollup_removes_pushed_and_void_legs_and_never_loses_the_ticket():
    status, profit, decimal_odds, american = rollup_ticket([_leg("WON", 150), _leg("PUSH", -110), _leg("VOID", 300)], 10)
    assert (status, profit, decimal_odds, american) == ("WON", 15.0, 2.5, 150)
    status, profit, decimal_odds, american = rollup_ticket([_leg("WON", 150), _leg("WON", -110)], 10)
    assert status == "WON"
    assert decimal_odds == pytest.approx(2.5 * (1 + 100 / 110))
    assert profit == to_cents(10 * decimal_odds - 10)
    # a loss still loses, even with pushes alongside
    assert rollup_ticket([_leg("LOST", 150), _leg("PUSH", 100)], 10)[:2] == ("LOST", -10.0)
    # pending legs keep the ticket open
    assert rollup_ticket([_leg("WON", 150), _leg("PENDING", 100)], 10)[0] == "PENDING"


def test_all_pushed_or_void_is_a_stake_refund():
    for legs in ([_leg("PUSH", 150), _leg("PUSH", -110)], [_leg("VOID", 150), _leg("PUSH", 200)]):
        status, profit, decimal_odds, american = rollup_ticket(legs, 25)
        assert status == "PUSHED"
        assert profit == 0.0
        assert decimal_odds == 1.0 and american is None


def test_rollup_profit_has_no_float_drift():
    legs = [_leg("WON", 110), _leg("WON", 110), _leg("WON", 110)]
    status, profit, decimal_odds, _american = rollup_ticket(legs, 33.33)
    assert status == "WON"
    assert profit == to_cents(33.33 * decimal_odds - 33.33)
    assert round(profit, 2) == profit


def _box(home=3, away=3, skaters=None):
    return BoxScore(
        game_id="2026020001",
        game_state="OFF",
        home_abbrev="DET",
        away_abbrev="NJD",
        home_score=home,
        away_score=away,
        skaters=skaters or [],
    )


def _grade(market, side, line, box, team="DET", **extra):
    leg = SimpleNamespace(
        market_type=market, side=side, line=line, team_abbrev=team, player_id=extra.get("player_id"), target_entity=extra.get("name", "")
    )
    return grade_leg(leg, box)[0]


def test_integer_lines_push_for_totals_spreads_and_props():
    box = _box(home=4, away=2)
    assert _grade("TOTAL", "OVER", 6.0, box) == "PUSH"
    assert _grade("TOTAL", "UNDER", 6.0, box) == "PUSH"
    assert _grade("SPREAD", "HOME", -2.0, box, team="DET") == "PUSH"
    assert _grade("PUCKLINE", "AWAY", 2.0, box, team="NJD") == "PUSH"
    assert _grade("TEAM_TOTAL", "OVER", 4.0, box, team="DET") == "PUSH"
    skater = SkaterLine(player_id=7, name="A. Player", team_abbrev="DET", goals=1, points=3, sog=3)
    box = _box(skaters=[skater])
    assert _grade("SOG", "OVER", 3.0, box, player_id=7, name="A. Player") == "PUSH"
    assert _grade("POINTS", "UNDER", 3.0, box, player_id=7, name="A. Player") == "PUSH"
    assert _grade("SOG", "OVER", 2.5, box, player_id=7, name="A. Player") == "WON"


def test_unknown_markets_stay_pending_instead_of_voiding():
    # A void would drop a possibly losing leg from the ticket.
    assert _grade("HITS", "OVER", 2.5, _box()) == "PENDING"
    assert _grade("CUSTOM", "YES", None, _box()) == "PENDING"


def test_player_with_no_row_for_a_yardage_category_is_zero_not_void():
    box = _box(skaters=[SkaterLine(player_id=9, name="Pat Receiver", team_abbrev="DET", stats={"REC_YDS": 40.0})])
    assert _grade("RUSH_YDS", "OVER", 5.5, box, player_id=9, name="Pat Receiver") == "LOST"
    assert _grade("ANYTIME_TD", "YES", 0.5, box, player_id=9, name="Pat Receiver") == "LOST"
    assert _grade("SOG", "OVER", 2.5, _box(), player_id=99, name="Ghost Player") == "VOID"


# ---------- ledger settlement ----------


def _built(legs, stake=20.0, american=300, decimal_odds=4.0):
    return BuiltParlay(
        risk_tier="Balanced",
        legs=legs,
        american_odds=american,
        decimal_odds=decimal_odds,
        model_prob=0.2,
        ev=0.0,
        synergy_score=0,
        rationale_summary="test",
        within_odds_band=True,
        recommended_stake=1.0,
        guideline_low=1.0,
        guideline_high=2.0,
        kelly_fraction=0.01,
        exposure_pct=0.02,
        stake=stake,
        potential_payout=payout_amount(stake, decimal_odds),
        net_profit_if_win=profit_amount(stake, decimal_odds),
        entry_edge=0.0,
    )


def _market_leg(leg_id, market, side, line, american, *, game_id="2026020001", team="DET", sport=None, player_id=None, entity="X"):
    return MarketLeg(
        leg_id=leg_id,
        game_id=game_id,
        matchup="NJD @ DET",
        game_date="2026-10-22",
        start_time_utc="2026-10-22T23:00:00Z",
        market_type=market,
        market_key=f"{game_id}|{leg_id}",
        target_entity=entity,
        team_abbrev=team,
        player_id=player_id,
        selection=f"{entity} {side} {line}",
        side=side,
        line=line,
        american_odds=american,
        decimal_odds=american_to_decimal(american),
        implied_prob=0.5,
        fair_prob=0.5,
        model_prob=0.5,
        ev=0.0,
        bookmaker="test",
        rationale="",
        sport=sport,
    )


def test_ticket_with_a_pushed_leg_pays_on_the_remaining_legs(tmp_path):
    session = open_session(tmp_path / "ledger.db")
    set_balance(session, 1000)
    legs = [
        _market_leg("tot", "TOTAL", "OVER", 6.0, 150, team=None),  # 4-2 final: exactly 6 -> push
        _market_leg("ml", "MONEYLINE", "HOME", None, 100),  # DET wins -> won
    ]
    built = _built(legs, stake=20.0, american=400, decimal_odds=5.0)
    save_parlay(session, built, placed_real=True, odds_source="test", sport="NHL")
    assert get_balance(session) == 980.0

    settled = settle_pending(session, lambda game_id: _box(home=4, away=2))
    assert settled == 1
    ticket = list_parlays(session)[0]
    assert ticket.status == "WON"
    assert {leg.leg_status for leg in ticket.legs} == {"PUSH", "WON"}
    assert ticket.settled_decimal_odds == 2.0 and ticket.settled_american_odds == 100
    assert ticket.net_profit == 20.0
    assert ticket.potential_payout == 40.0
    assert get_balance(session) == 1020.0  # 980 + 40 payout
    for leg in ticket.legs:
        assert leg.settled_at is not None and leg.settled_at.tzinfo is not None
        assert leg.closing_line is None and leg.closing_odds is None  # no feed, so NULL, never invented
    assert ticket.closing_line_value is None
    session.close()


def test_all_legs_push_refunds_the_stake_and_does_not_lose(tmp_path):
    session = open_session(tmp_path / "ledger.db")
    set_balance(session, 500)
    legs = [_market_leg("t1", "TOTAL", "OVER", 6.0, 150, team=None), _market_leg("s1", "SPREAD", "HOME", -2.0, 120)]
    save_parlay(session, _built(legs, stake=50.0, american=500, decimal_odds=6.0), placed_real=True, odds_source="test")
    assert get_balance(session) == 450.0
    settle_pending(session, lambda game_id: _box(home=4, away=2))
    ticket = list_parlays(session)[0]
    assert ticket.status == "PUSHED"
    assert ticket.net_profit == 0.0
    assert ticket.potential_payout == 50.0
    assert get_balance(session) == 500.0
    session.close()


def test_closing_prices_are_stored_only_when_the_provider_has_them(tmp_path):
    session = open_session(tmp_path / "ledger.db")
    set_balance(session, 500)
    legs = [_market_leg("ml", "MONEYLINE", "HOME", None, 100)]
    save_parlay(session, _built(legs, stake=10.0, american=100, decimal_odds=2.0), placed_real=False, odds_source="test")

    def closing(leg):
        return (None, -150)

    settle_pending(session, lambda game_id: _box(home=4, away=2), closing)
    ticket = list_parlays(session)[0]
    assert ticket.legs[0].closing_odds == -150
    assert ticket.legs[0].closing_line is None
    assert ticket.closing_line_value == pytest.approx(american_to_implied(-150) - american_to_implied(100))
    session.close()


def test_timestamps_are_utc_aware_and_set_at_settlement(tmp_path):
    session = open_session(tmp_path / "ledger.db")
    set_balance(session, 500)
    save_parlay(session, _built([_market_leg("ml", "MONEYLINE", "HOME", None, 100)], stake=10.0), placed_real=False, odds_source="test")
    created = list_parlays(session)[0]
    assert created.created_at.tzinfo is not None and created.created_at.utcoffset() == timezone.utc.utcoffset(None)
    assert created.settled_at is None and created.legs[0].settled_at is None
    settle_pending(session, lambda game_id: _box(home=4, away=2))
    done = list_parlays(session)[0]
    assert done.settled_at is not None and done.settled_at >= done.created_at
    assert done.legs[0].settled_at is not None
    session.close()


def test_settlement_routes_each_leg_and_never_sends_a_player_id_as_a_game_id(tmp_path):
    session = open_session(tmp_path / "ledger.db")
    set_balance(session, 500)
    legs = [
        _market_leg("nhl", "MONEYLINE", "HOME", None, 100, game_id="2026020001", sport="NHL"),
        _market_leg("nba", "REBOUNDS", "OVER", 8.5, 100, game_id="401810001", sport="NBA", entity="Big Man"),
        _market_leg("bad", "REBOUNDS", "OVER", 8.5, 100, game_id="6450", sport="NBA", entity="Player Id Leg"),
        _market_leg("typed", "CUSTOM", "YES", None, 100, game_id="typed-abc", sport="NFL"),
    ]
    save_parlay(session, _built(legs, stake=5.0), placed_real=False, odds_source="test", sport="MIX")
    calls = []

    def fetch(game_id, sport):
        calls.append((game_id, sport))
        return None

    settle_pending(session, fetch)
    assert sorted(calls) == [("2026020001", "NHL"), ("401810001", "NBA")]
    ticket = list_parlays(session)[0]
    notes = {leg.market_type + leg.game_id: leg.settlement_notes for leg in ticket.legs}
    assert "settled by hand" in notes["REBOUNDS6450"]
    assert "settled by hand" in notes["CUSTOMtyped-abc"]
    assert ticket.status == "PENDING"
    session.close()


def test_route_leg_rules():
    parlay = SimpleNamespace(sport="NBA")
    assert route_leg(SimpleNamespace(market_type="TOTAL", game_id="2026020001", sport="NHL"), parlay) == "NHL"
    assert route_leg(SimpleNamespace(market_type="TOTAL", game_id="401810001", sport="NFL"), parlay) == "NFL"
    assert route_leg(SimpleNamespace(market_type="TOTAL", game_id="401810001", sport="MIX"), parlay) == "NBA"
    assert route_leg(SimpleNamespace(market_type="TOTAL", game_id="401810001", sport="MIX"), SimpleNamespace(sport="MIX")) is None
    assert route_leg(SimpleNamespace(market_type="TOTAL", game_id="6450", sport="NBA"), parlay) is None
    assert route_leg(SimpleNamespace(market_type="CUSTOM", game_id="2026020001", sport="NHL"), parlay) is None
    assert route_leg(SimpleNamespace(market_type="TOTAL", game_id="nba-mock-1", sport="NBA"), parlay) is None


def test_a_real_wager_cannot_exceed_or_ignore_the_bankroll(tmp_path):
    session = open_session(tmp_path / "ledger.db")
    set_balance(session, 100)
    leg = _market_leg("ml", "MONEYLINE", "HOME", None, 100)
    with pytest.raises(ValueError):
        save_parlay(session, _built([leg], stake=150.0), placed_real=True, odds_source="test")
    with pytest.raises(ValueError):
        save_parlay(session, _built([leg], stake=0.0), placed_real=True, odds_source="test")
    assert get_balance(session) == 100.0
    save_parlay(session, _built([leg], stake=0.0), placed_real=False, odds_source="test")  # paper tickets are unaffected
    with pytest.raises(ValueError):
        set_balance(session, -5)
    session.close()


def test_legs_keep_their_own_league_at_save_time(tmp_path):
    session = open_session(tmp_path / "ledger.db")
    set_balance(session, 100)
    legs = [
        _market_leg("a", "MONEYLINE", "HOME", None, 100, sport="NBA", game_id="401810001"),
        _market_leg("b", "MONEYLINE", "HOME", None, 100, game_id="2026020001"),
    ]
    save_parlay(session, _built(legs, stake=5.0), placed_real=False, odds_source="test", sport="MIX")
    sports = {leg.game_id: leg.sport for leg in list_parlays(session)[0].legs}
    assert sports == {"401810001": "NBA", "2026020001": "MIX"}
    session.close()


def test_migration_adds_nullable_leg_columns_without_touching_rows(tmp_path):
    path = tmp_path / "old.db"
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE parlays (id VARCHAR PRIMARY KEY, created_at DATETIME, risk_tier VARCHAR, total_american_odds INTEGER,
            total_decimal_odds FLOAT, stake_amount FLOAT, potential_payout FLOAT, user_placed_bet BOOLEAN, status VARCHAR,
            settled_at DATETIME, net_profit FLOAT, closing_line_value FLOAT, rationale_summary TEXT, entry_edge FLOAT,
            model_prob FLOAT, ev FLOAT, synergy_notes TEXT, odds_source VARCHAR, sport VARCHAR, settled_decimal_odds FLOAT,
            settled_american_odds INTEGER);
        CREATE TABLE parlay_legs (id VARCHAR PRIMARY KEY, parlay_id VARCHAR, game_id VARCHAR, matchup VARCHAR, game_date VARCHAR,
            market_type VARCHAR, target_entity VARCHAR, selection VARCHAR, line FLOAT, american_odds INTEGER, model_prob FLOAT,
            fair_prob FLOAT, ev FLOAT, actual_result FLOAT, leg_status VARCHAR, settlement_notes TEXT, side VARCHAR,
            player_id INTEGER, team_abbrev VARCHAR, bookmaker VARCHAR, rationale TEXT, sport VARCHAR);
        INSERT INTO parlays (id, created_at, risk_tier, total_american_odds, total_decimal_odds, stake_amount, potential_payout,
            user_placed_bet, status, net_profit, sport) VALUES ('p1', '2026-10-01 12:00:00', 'Balanced', 300, 4.0, 10.0, 40.0, 0, 'PENDING', 0.0, 'NHL');
        INSERT INTO parlay_legs (id, parlay_id, game_id, matchup, game_date, market_type, target_entity, selection, american_odds, leg_status, side, sport)
            VALUES ('l1', 'p1', '2026020001', 'A @ B', '2026-10-01', 'TOTAL', 'x', 'Over 5.5', 100, 'PENDING', 'OVER', 'NHL');
        """
    )
    connection.commit()
    connection.close()

    session = open_session(path)
    leg = session.get(ParlayLeg, "l1")
    assert leg.closing_line is None and leg.closing_odds is None and leg.settled_at is None
    assert leg.selection == "Over 5.5" and leg.american_odds == 100
    parlay = list_parlays(session)[0]
    assert parlay.created_at.isoformat().startswith("2026-10-01T12:00:00") and parlay.created_at.tzinfo is not None
    session.close()
    columns = {row[1] for row in sqlite3.connect(path).execute("PRAGMA table_info(parlay_legs)")}
    assert {"closing_line", "closing_odds", "settled_at"} <= columns

