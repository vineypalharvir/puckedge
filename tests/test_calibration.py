"""Market-anchored chances, duplicate-row devig, synthetic zero edge, and ticket labels."""

import pytest

from src.api.data_models import (
    Game,
    MarketLeg,
    Offer,
    PlayerProfile,
    SlateContext,
    TeamProfile,
    TeamSide,
)
from src.engine import stats_analyzer
from src.engine.devig import american_to_decimal, american_to_implied, expected_value, multiplicative_devig
from src.engine.generator import (
    ESTIMATED_TAG,
    PROFILES,
    _assemble,
    generate_parlays,
    neutralize_synthetic,
)
from src.services.slate import load_board
from src.engine.stats_analyzer import MAX_EDGE_POINTS, price_offers


def _slate(games_played: int = 8, sog_rate: float = 4.6) -> SlateContext:
    game = Game(
        game_id="g1",
        game_date="2026-10-22",
        start_time_utc="2026-10-22T23:00:00Z",
        game_state="FUT",
        away=TeamSide(team_id=2, abbrev="NSH", name="Nashville Predators"),
        home=TeamSide(team_id=1, abbrev="BOS", name="Boston Bruins"),
    )
    strong = TeamProfile(abbrev="BOS", name="Boston Bruins", games_played=games_played, wins=games_played, gf_per_game=4.4, ga_per_game=2.0, l10_gf_per_game=4.6, l10_ga_per_game=1.8)
    weak = TeamProfile(abbrev="NSH", name="Nashville Predators", games_played=games_played, losses=games_played, gf_per_game=2.0, ga_per_game=4.0, l10_gf_per_game=1.8, l10_ga_per_game=4.2)
    star = PlayerProfile(
        player_id=7,
        name="Test Star",
        team_abbrev="BOS",
        position="C",
        games_played=games_played,
        sog_per_game=sog_rate,
        l10_sog=sog_rate,
    )
    return SlateContext(slate_date="2026-10-22", source="test", games=[game], teams={"BOS": strong, "NSH": weak}, players=[star], goalies=[])


def _offer(**kwargs) -> Offer:
    base = dict(
        game_id="g1",
        matchup="NSH @ BOS",
        game_date="2026-10-22",
        start_time_utc="2026-10-22T23:00:00Z",
        market_type="SOG",
        market_key="g1|SOG|7|3.5",
        target_entity="Test Star",
        player_id=7,
        team_abbrev="BOS",
        opponent_abbrev="NSH",
        selection="Test Star Over 3.5",
        side="OVER",
        line=3.5,
        american_odds=120,
        bookmaker="draftkings",
    )
    base.update(kwargs)
    return Offer(**base)


def test_duplicate_rows_do_not_pull_the_fair_price_down():
    over = _offer()
    under = _offer(side="UNDER", selection="Test Star Under 3.5", american_odds=-150, selectable=False)
    legs = price_offers(_slate(), [over, over.model_copy(), under])
    assert len(legs) == 2
    leg = next(item for item in legs if item.side == "OVER")
    expected = multiplicative_devig([american_to_implied(120), american_to_implied(-150)])[0]
    assert leg.fair_prob == pytest.approx(expected)
    assert leg.fair_prob > 0.40


def test_small_sample_player_chance_stays_near_the_market():
    over = _offer()
    under = _offer(side="UNDER", selection="Test Star Under 3.5", american_odds=-150, selectable=False)
    leg = next(item for item in price_offers(_slate(games_played=8), [over, under]) if item.side == "OVER")
    assert abs(leg.model_prob - leg.fair_prob) <= MAX_EDGE_POINTS + 1e-9
    assert "blends" in leg.rationale
    # More games of evidence give the model more weight.
    seasoned = next(item for item in price_offers(_slate(games_played=82), [over, under]) if item.side == "OVER")
    assert seasoned.model_prob > leg.model_prob


def test_rate_is_pulled_toward_the_role_average():
    slate = _slate(games_played=4, sog_rate=6.0)
    rate = stats_analyzer._shrunk_rate(6.0, slate.players[0], "sog")
    assert 2.2 < rate < 6.0
    assert rate == pytest.approx((4 * 6.0 + stats_analyzer.RATE_PRIOR_GAMES * 2.2) / (4 + stats_analyzer.RATE_PRIOR_GAMES))


def test_moneyline_cannot_run_far_from_the_market():
    home = _offer(market_type="MONEYLINE", market_key="g1|MONEYLINE", target_entity="Boston Bruins", player_id=None, selection="Boston Bruins ML", side="HOME", line=None, american_odds=-105)
    away = home.model_copy(update={"side": "AWAY", "selection": "Nashville Predators ML", "american_odds": -115, "team_abbrev": "NSH", "opponent_abbrev": "BOS"})
    leg = next(item for item in price_offers(_slate(), [home, away]) if item.side == "HOME")
    assert leg.model_prob - leg.fair_prob <= MAX_EDGE_POINTS + 1e-9
    assert leg.model_prob < 0.7


def _synthetic_leg(**kwargs) -> MarketLeg:
    base = dict(
        leg_id="s",
        game_id="g1",
        matchup="A @ B",
        game_date="2026-10-22",
        start_time_utc="2026-10-22T23:00:00Z",
        market_type="POINTS",
        market_key="g1|POINTS|1|0.5",
        target_entity="Skater",
        player_id=1,
        selection="Skater Over 0.5 points",
        side="OVER",
        line=0.5,
        american_odds=-275,
        decimal_odds=american_to_decimal(-275),
        implied_prob=0.733,
        fair_prob=0.702,
        model_prob=0.743,
        ev=0.013,
        bookmaker="synthetic-bet365",
        rationale="test",
    )
    base.update(kwargs)
    return MarketLeg(**base)


def test_synthetic_leg_has_zero_edge_and_an_estimated_tag():
    leg = _synthetic_leg()
    [neutral] = neutralize_synthetic([leg])
    assert neutral.model_prob == pytest.approx(leg.fair_prob)
    assert neutral.ev == pytest.approx(expected_value(leg.fair_prob, leg.decimal_odds))
    assert neutral.ev < 0
    assert ESTIMATED_TAG in neutral.tags
    assert leg.model_prob == 0.743  # the input leg is untouched
    real = _synthetic_leg(bookmaker="draftkings")
    assert neutralize_synthetic([real])[0].model_prob == real.model_prob


def test_ticket_edge_is_the_whole_ticket_ev_and_stake_pct_is_the_real_stake():
    legs = neutralize_synthetic(
        [
            _synthetic_leg(leg_id="a", market_key="g1|A", player_id=1, bookmaker="draftkings", american_odds=120, decimal_odds=2.2, model_prob=0.66, fair_prob=0.43, implied_prob=0.4545, ev=0.45),
            _synthetic_leg(leg_id="b", market_key="g2|B", game_id="g2", player_id=2, bookmaker="draftkings", american_odds=-105, decimal_odds=american_to_decimal(-105), model_prob=0.72, fair_prob=0.49, implied_prob=0.512, ev=0.41),
            _synthetic_leg(leg_id="c", market_key="g3|C", game_id="g3", player_id=3),
        ]
    )
    probability = 1.0
    decimal = 1.0
    for leg in legs:
        probability *= leg.model_prob
        decimal *= leg.decimal_odds
    ticket = _assemble(legs, PROFILES["Balanced"], american=486, decimal_odds=decimal, probability=probability, in_band=True, relaxed=False, bankroll=20.0, stake=20.0)
    assert ticket.entry_edge == pytest.approx(probability * decimal - 1.0)
    assert ticket.entry_edge == pytest.approx(ticket.ev)
    assert ticket.recommended_pct == pytest.approx(ticket.recommended_stake / 20.0, abs=0.003)
    assert ticket.recommended_pct <= PROFILES["Balanced"].stake_high + 0.003
    assert ticket.recommended_pct <= ticket.kelly_fraction or ticket.kelly_fraction == 0
    # The estimated leg adds no edge: its chance is its own no-vig price.
    assert legs[2].model_prob == pytest.approx(0.702)
    assert "estimated price" in ticket.rationale_summary


def test_mock_board_tickets_never_rank_on_made_up_edges():
    board = load_board("2026-10-13", prefer_live=False)
    for ticket in generate_parlays(board.legs, "Balanced", bankroll=20.0, stake=20.0):
        assert ticket.entry_edge == pytest.approx(ticket.ev)
        assert ticket.ev < 0.0  # every leg on the mock board is synthetic, so no leg carries edge
        assert all(leg.model_prob == pytest.approx(leg.fair_prob) for leg in ticket.legs)
