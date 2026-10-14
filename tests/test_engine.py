from src.api.data_models import MarketLeg
from src.engine.correlation import pair_conflicts, synergy, ticket_conflicts
from src.engine.generator import PROFILES, generate_parlays
from src.services.slate import load_board


def _leg(**kwargs) -> MarketLeg:
    base = dict(
        leg_id="x",
        game_id="g1",
        matchup="A @ B",
        game_date="2026-10-13",
        start_time_utc="2026-10-13T23:00:00Z",
        market_type="MONEYLINE",
        market_key="g1|MONEYLINE",
        target_entity="Home",
        selection="Home ML",
        side="HOME",
        american_odds=-120,
        decimal_odds=1.833,
        implied_prob=0.55,
        fair_prob=0.52,
        model_prob=0.58,
        ev=0.04,
        bookmaker="synthetic-bet365",
        rationale="test",
    )
    base.update(kwargs)
    return MarketLeg(**base)


def test_opposing_moneylines_and_low_total_stacks_are_rejected():
    home = _leg(leg_id="h", side="HOME", team_abbrev="HOM", market_key="g1|MONEYLINE")
    away = _leg(
        leg_id="a",
        side="AWAY",
        team_abbrev="AWY",
        market_key="g1|MONEYLINE",
        selection="Away ML",
        target_entity="Away",
    )
    assert pair_conflicts(home, away)
    nested = _leg(
        leg_id="pl",
        market_type="PUCKLINE",
        market_key="g1|PUCK",
        side="HOME",
        line=-1.5,
        team_abbrev="HOM",
        selection="Home -1.5",
    )
    assert pair_conflicts(home, nested)
    under = _leg(
        leg_id="u",
        market_type="TOTAL",
        market_key="g1|TOTAL|5.5",
        side="UNDER",
        line=5.5,
        selection="Under 5.5",
    )
    scorer_a = _leg(
        leg_id="s1",
        market_type="GOALSCORER",
        market_key="g1|GOAL|1",
        side="YES",
        player_id=1,
        selection="A anytime",
    )
    scorer_b = _leg(
        leg_id="s2",
        market_type="GOALSCORER",
        market_key="g1|GOAL|2",
        side="YES",
        player_id=2,
        selection="B anytime",
    )
    assert ticket_conflicts([under, scorer_a, scorer_b])


def test_puckline_and_team_total_score_as_synergy():
    cover = _leg(
        leg_id="c",
        market_type="PUCKLINE",
        market_key="g1|PUCK|a",
        side="HOME",
        line=-1.5,
        team_abbrev="HOM",
        target_entity="Home Team",
        selection="Home Team -1.5",
    )
    team_over = _leg(
        leg_id="t",
        market_type="TEAM_TOTAL",
        market_key="g1|TT|HOM|3.5",
        side="OVER",
        line=3.5,
        team_abbrev="HOM",
        target_entity="Home Team",
        selection="Home Team Over 3.5",
    )
    score, notes = synergy([cover, team_over])
    assert score >= 2
    assert notes


def test_each_risk_tier_builds_parlays_from_the_mock_slate():
    board = load_board("2026-10-13", prefer_live=False)
    assert board.context.source == "mock"
    assert len(board.context.games) >= 6
    assert any(leg.ev > 0 for leg in board.legs)
    for tier, profile in PROFILES.items():
        tickets = generate_parlays(board.legs, tier, bankroll=1000, stake=25)
        assert tickets, tier
        assert any(ticket.within_odds_band for ticket in tickets), tier
        if tier == "Krazy":
            assert any(ticket.decimal_odds >= 100 for ticket in tickets)
        for ticket in tickets:
            assert profile.min_legs <= len(ticket.legs) <= profile.max_legs
            assert ticket.potential_payout > ticket.stake
            assert all(leg.model_prob < 0.97 for leg in ticket.legs)
            assert ticket.exposure_warning is None or ticket.exposure_pct > 0.05
