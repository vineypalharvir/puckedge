"""Tickets that mix leagues, or whose legs lack a saved game id, must still settle."""

from datetime import datetime, timezone

from src.api.data_models import BoxScore, MarketLeg, SkaterLine, BuiltParlay
from src.tracker.database import open_session
from src.tracker.models import Parlay, ParlayLeg
from src.tracker.repository import settle_pending


def _leg(idx, *, game_id, matchup, market, side, line, entity, player_id=None, sport="NHL", date="2026-10-22"):
    return ParlayLeg(
        id=f"t-{idx}",
        parlay_id="t",
        game_id=game_id,
        matchup=matchup,
        game_date=date,
        market_type=market,
        target_entity=entity,
        selection=f"{entity} {side} {line}",
        line=line,
        american_odds=-110,
        leg_status="PENDING",
        side=side,
        player_id=player_id,
        sport=sport,
    )


def _ticket(session, legs, sport="NHL"):
    session.add(
        Parlay(
            id="t",
            created_at=datetime(2026, 10, 22, 18, tzinfo=timezone.utc),
            risk_tier="Balanced",
            total_american_odds=495,
            total_decimal_odds=5.95,
            stake_amount=10,
            potential_payout=59.5,
            user_placed_bet=False,
            status="PENDING",
            sport=sport,
        )
    )
    for leg in legs:
        session.add(leg)
    session.commit()


NBA_BOX = BoxScore(game_id="401", game_state="FINAL", home_abbrev="PHI", away_abbrev="CLE", home_score=70, away_score=60)
NFL_BOX = BoxScore(
    game_id="501",
    game_state="FINAL",
    home_abbrev="ATL",
    away_abbrev="SF",
    home_score=20,
    away_score=24,
    skaters=[SkaterLine(player_id=9, name="Brock Purdy", team_abbrev="SF", stats={"PASS_YDS": 280.0})],
)
NHL_BOX = BoxScore(
    game_id="2026020100",
    game_state="OFF",
    home_abbrev="VAN",
    away_abbrev="DET",
    home_score=2,
    away_score=3,
    skaters=[SkaterLine(player_id=1, name="A. DeBrincat", team_abbrev="DET", sog=4)],
)


def _fetch(game_id, sport="NHL"):
    table = {("NBA", "401"): NBA_BOX, ("NFL", "501"): NFL_BOX, ("NHL", "2026020100"): NHL_BOX}
    box = table.get((sport, game_id))
    if box is None:
        raise RuntimeError(f"no {sport} game {game_id}")
    return box


def _find(sport, away, home, days):
    index = {("NBA", "CLE", "PHI"): "401", ("NFL", "SF", "ATL"): "501", ("NHL", "DET", "VAN"): "2026020100"}
    return index.get((sport, away, home))


def test_mixed_league_ticket_with_missing_game_ids_settles(tmp_path):
    session = open_session(tmp_path / "mixed.db")
    legs = [
        _leg(0, game_id="", matchup="CLE @ PHI", market="TOTAL", side="UNDER", line=145.5, entity="Total"),
        _leg(1, game_id="501", matchup="SF @ ATL", market="PASS_YDS", side="OVER", line=250.5, entity="Brock Purdy"),
        _leg(2, game_id="2026020100", matchup="DET @ VAN", market="SOG", side="OVER", line=3.5, entity="Alex DeBrincat", player_id=1),
    ]
    _ticket(session, legs, sport="NHL")

    settle_pending(session, _fetch, _find)

    row = session.get(Parlay, "t")
    by_idx = {leg.id: leg for leg in row.legs}
    assert [by_idx[f"t-{i}"].leg_status for i in range(3)] == ["WON", "WON", "WON"]
    assert by_idx["t-0"].game_id == "401"
    assert by_idx["t-0"].sport == "NBA"
    assert by_idx["t-1"].sport == "NFL"
    assert row.status == "WON"
    session.close()


def test_unfindable_leg_stays_pending_with_a_reason(tmp_path):
    session = open_session(tmp_path / "gone.db")
    legs = [_leg(0, game_id="", matchup="AAA @ BBB", market="TOTAL", side="OVER", line=5.5, entity="Total")]
    _ticket(session, legs)

    settle_pending(session, _fetch, _find)

    leg = session.get(Parlay, "t").legs[0]
    assert leg.leg_status == "PENDING"
    assert "AAA @ BBB" in leg.settlement_notes
    session.close()
