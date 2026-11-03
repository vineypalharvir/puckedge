from types import SimpleNamespace

from src.api.data_models import Game, Offer, TeamSide
from src.api.odds_client import available_books, fit_odds_query, offers_for_book, parse_odds_payload, parse_prop_payload, pick_prop_offers
from src.services.slate import _extra_prop_legs


def _game() -> Game:
    return Game(
        game_id="1",
        game_date="2026-10-13",
        start_time_utc="2026-10-13T23:00:00Z",
        game_state="FUT",
        away=TeamSide(team_id=1, abbrev="NJD", name="New Jersey Devils"),
        home=TeamSide(team_id=2, abbrev="DET", name="Detroit Red Wings"),
    )


def test_odds_query_shrinks_to_remaining_credits():
    assert fit_odds_query("us,uk,eu", "h2h,spreads,totals", 7) == ("us,uk", "h2h,spreads,totals")
    assert fit_odds_query("us,uk,eu", "h2h,spreads,totals", 2) == ("us", "h2h,spreads")
    assert fit_odds_query("us,uk,eu", "h2h,spreads,totals", 0) is None


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


def test_prop_is_kept_when_the_player_is_not_on_the_roster():
    game = Game(
        game_id="nfl-1",
        game_date="2026-10-15",
        start_time_utc="2026-10-15T20:00:00Z",
        game_state="FUT",
        away=TeamSide(team_id=1, abbrev="GB", name="Green Bay Packers"),
        home=TeamSide(team_id=2, abbrev="DEN", name="Denver Broncos"),
    )
    offers = parse_prop_payload(
        {
            "bookmakers": [
                {
                    "key": "draftkings",
                    "markets": [
                        {
                            "key": "player_anytime_td",
                            "outcomes": [{"name": "Emanuel Wilson", "price": 140}],
                        }
                    ],
                }
            ]
        },
        game,
        [],
    )
    assert len(offers) == 1
    assert offers[0].target_entity == "Emanuel Wilson"
    assert offers[0].market_type == "ANYTIME_TD"


def _prop(book: str, name: str = "Emanuel Wilson") -> Offer:
    return Offer(
        game_id="nfl-1",
        matchup="SEA @ DEN",
        game_date="2026-10-15",
        start_time_utc="",
        market_type="ANYTIME_TD",
        market_key="nfl-1|ANYTIME_TD|wilson",
        target_entity=name,
        selection=f"{name} anytime TD",
        side="YES",
        line=0.5,
        american_odds=140,
        bookmaker=book,
    )


def test_prop_from_another_book_is_kept_when_bet365_did_not_post_it():
    offers = [_prop("draftkings"), _prop("fanduel", "J.K. Dobbins")]
    picked = pick_prop_offers(offers, "bet365")
    assert {offer.target_entity for offer in picked} == {"Emanuel Wilson", "J.K. Dobbins"}
    wilson = next(offer for offer in picked if offer.target_entity == "Emanuel Wilson")
    assert wilson.bookmaker == "draftkings"


def test_selected_book_wins_when_it_posted_the_prop():
    offers = [_prop("draftkings"), _prop("bet365")]
    offers[0].american_odds = 150
    offers[1].american_odds = 130
    picked = pick_prop_offers(offers, "bet365")
    assert len(picked) == 1
    assert picked[0].bookmaker == "bet365"
    assert picked[0].american_odds == 130


def test_missing_prop_feed_asks_for_typed_odds():
    context = SimpleNamespace(notes=[])
    assert _extra_prop_legs(context, [], "bet365", [], lambda _context, offers: offers) == []
    assert "Type that leg's odds" in context.notes[0]
