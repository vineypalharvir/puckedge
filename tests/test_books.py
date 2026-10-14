from src.api.data_models import Game, TeamSide
from src.api.odds_client import available_books, offers_for_book, parse_odds_payload


def _game() -> Game:
    return Game(
        game_id="1",
        game_date="2026-10-13",
        start_time_utc="2026-10-13T23:00:00Z",
        game_state="FUT",
        away=TeamSide(team_id=1, abbrev="NJD", name="New Jersey Devils"),
        home=TeamSide(team_id=2, abbrev="DET", name="Detroit Red Wings"),
    )


def test_every_returned_book_is_kept():
    payload = [
        {
            "home_team": "Detroit Red Wings",
            "away_team": "New Jersey Devils",
            "bookmakers": [
                {
                    "key": "draftkings",
                    "markets": [{"key": "h2h", "outcomes": [{"name": "Detroit Red Wings", "price": -120}, {"name": "New Jersey Devils", "price": 110}]}],
                },
                {
                    "key": "bet365",
                    "markets": [{"key": "h2h", "outcomes": [{"name": "Detroit Red Wings", "price": -115}, {"name": "New Jersey Devils", "price": 105}]}],
                },
                {
                    "key": "unibet",
                    "markets": [{"key": "h2h", "outcomes": [{"name": "Detroit Red Wings", "price": -118}, {"name": "New Jersey Devils", "price": 108}]}],
                },
            ],
        }
    ]
    offers = parse_odds_payload(payload, [_game()])
    books = available_books(offers)
    assert books[:2] == ["bet365", "draftkings"]
    assert "unibet" in books
    assert books[-1] == "synthetic-bet365"
    bet365 = offers_for_book(offers, "bet365")
    assert bet365
    assert {offer.bookmaker for offer in bet365} == {"bet365"}
    assert offers_for_book(offers, "synthetic-bet365") == []
