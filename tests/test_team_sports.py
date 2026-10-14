from src.engine.generator import PROFILES, generate_parlays
from src.services.slate import load_board


def test_nfl_and_nba_mock_boards_build_parlays():
    for sport in ("NFL", "NBA"):
        board = load_board("2026-10-14", prefer_live=False, sport=sport)
        assert board.context.sport == sport
        assert len(board.context.games) >= 4
        assert any(leg.market_type == "SPREAD" for leg in board.legs)
        tickets = generate_parlays(board.legs, "Balanced", bankroll=1000, stake=25)
        assert tickets, sport
        assert all(PROFILES["Balanced"].min_legs <= len(ticket.legs) <= PROFILES["Balanced"].max_legs for ticket in tickets)
