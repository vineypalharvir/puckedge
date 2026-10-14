from types import SimpleNamespace

from src.api.data_models import BoxScore, GoalieLine, SkaterLine
from src.engine.generator import generate_parlays
from src.services.slate import load_board
from src.tracker.database import open_session
from src.tracker.repository import get_balance, performance, save_parlay, set_balance, settle_pending
from src.tracker.settlement import grade_leg, rollup_ticket


def _box() -> BoxScore:
    return BoxScore(
        game_id="2026990001",
        game_state="OFF",
        home_abbrev="DET",
        away_abbrev="NJD",
        home_score=2,
        away_score=5,
        skaters=[
            SkaterLine(player_id=8470794, name="J. Hughes", team_abbrev="NJD", goals=1, assists=1, points=2, sog=4),
        ],
        goalies=[
            GoalieLine(player_id=1, name="J. Markstrom", team_abbrev="NJD", saves=31, shots_against=33, starter=True),
        ],
    )


def test_grades_moneyline_total_push_and_player_props():
    box = _box()
    away_ml = SimpleNamespace(market_type="MONEYLINE", side="AWAY", line=None, team_abbrev="NJD", player_id=None, target_entity="Devils")
    status, _actual, _notes = grade_leg(away_ml, box)
    assert status == "WON"

    total = SimpleNamespace(market_type="TOTAL", side="OVER", line=6.0, team_abbrev=None, player_id=None, target_entity="total")
    status, actual, _notes = grade_leg(total, box)
    assert status == "WON" and actual == 7
    push = SimpleNamespace(market_type="TOTAL", side="OVER", line=7.0, team_abbrev=None, player_id=None, target_entity="total")
    status, actual, _notes = grade_leg(push, box)
    assert status == "PUSH" and actual == 7

    puck = SimpleNamespace(market_type="PUCKLINE", side="AWAY", line=-1.5, team_abbrev="NJD", player_id=None, target_entity="Devils")
    status, margin, _notes = grade_leg(puck, box)
    assert status == "WON" and margin == 3

    sog = SimpleNamespace(market_type="SOG", side="OVER", line=2.5, team_abbrev="NJD", player_id=8470794, target_entity="Jack Hughes")
    status, actual, _notes = grade_leg(sog, box)
    assert status == "WON" and actual == 4

    missing = SimpleNamespace(market_type="SOG", side="OVER", line=2.5, team_abbrev="NJD", player_id=999, target_entity="Scratch Player")
    status, _actual, _notes = grade_leg(missing, box)
    assert status == "VOID"


def test_push_reduces_the_ticket_and_a_loss_still_loses():
    won = SimpleNamespace(leg_status="WON", american_odds=150)
    push = SimpleNamespace(leg_status="PUSH", american_odds=-110)
    lost = SimpleNamespace(leg_status="LOST", american_odds=-110)
    status, profit, decimal_odds, american = rollup_ticket([won, push], stake=10)
    assert status == "WON"
    assert decimal_odds == 2.5
    assert american == 150
    assert profit == 15
    status, profit, _decimal, _american = rollup_ticket([won, lost], stake=10)
    assert status == "LOST" and profit == -10


def test_ledger_saves_a_paper_ticket_and_settles_it(tmp_path):
    board = load_board("2026-10-13", prefer_live=False)
    tickets = generate_parlays(board.legs, "Conservative", bankroll=1000, stake=20)
    assert tickets
    session = open_session(tmp_path / "ledger.db")
    set_balance(session, 1000)
    save_parlay(session, tickets[0], placed_real=False, odds_source=board.odds_source)
    real = generate_parlays(board.legs, "Balanced", bankroll=1000, stake=15)
    if real:
        save_parlay(session, real[0], placed_real=True, odds_source=board.odds_source)
        assert get_balance(session) == 985

    def _fetch(_game_id: str):
        return _box()

    settle_pending(session, _fetch)
    summary = performance(session)
    assert summary["tickets"] >= 1
    assert summary["pending"] == 0 or summary["wins"] + summary["losses"] + summary["pushes"] >= 1
    session.close()
