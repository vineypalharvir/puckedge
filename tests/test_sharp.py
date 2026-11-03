from src.api.data_models import Game, TeamSide
from src.api.sharp_client import parse_sharp_rows


def _game() -> Game:
    return Game(
        game_id="401909093",
        game_date="2026-10-22",
        start_time_utc="2026-10-22T23:00:00Z",
        game_state="FUT",
        away=TeamSide(team_id=1, abbrev="CLE", name="Cleveland Cavaliers"),
        home=TeamSide(team_id=2, abbrev="PHI", name="Philadelphia 76ers"),
    )


def test_sharp_main_line_maps_short_team_names_and_skips_alts():
    rows = [
        {
            "sportsbook": "draftkings",
            "event_id": "33483153",
            "home_team": "PHI 76ers",
            "away_team": "CLE Cavaliers",
            "market_type": "total_points",
            "selection": "Under",
            "selection_type": "under",
            "odds_american": -110,
            "line": 237.5,
            "is_main_line": True,
        },
        {
            "sportsbook": "draftkings",
            "event_id": "33483153",
            "home_team": "PHI 76ers",
            "away_team": "CLE Cavaliers",
            "market_type": "total_points",
            "selection": "Under",
            "selection_type": "under",
            "odds_american": -108,
            "line": 145.5,
            "is_main_line": False,
        },
        {
            "sportsbook": "fanduel",
            "event_id": "33483153",
            "home_team": "PHI 76ers",
            "away_team": "CLE Cavaliers",
            "market_type": "moneyline",
            "selection": "PHI 76ers",
            "selection_type": "home",
            "odds_american": -150,
            "line": None,
            "is_main_line": True,
        },
    ]
    offers = parse_sharp_rows(rows, [_game()], [], "NBA")
    assert len(offers) == 2
    total = next(offer for offer in offers if offer.market_type == "TOTAL")
    assert total.line == 237.5
    assert total.american_odds == -110
    assert total.bookmaker == "draftkings"
    assert total.selection == "Under 237.5"
    money = next(offer for offer in offers if offer.market_type == "MONEYLINE")
    assert money.side == "HOME"
    assert money.team_abbrev == "PHI"


# --- duplicate rows and paging -------------------------------------------------

import httpx  # noqa: E402

from src.api import sharp_client  # noqa: E402
from src.api.data_models import PlayerProfile  # noqa: E402

TEAMS = [
    ("Boston Bruins", "BOS", "Nashville Predators", "NSH"),
    ("Ottawa Senators", "OTT", "San Jose Sharks", "SJS"),
    ("Seattle Kraken", "SEA", "Utah Mammoth", "UTA"),
    ("Dallas Stars", "DAL", "Winnipeg Jets", "WPG"),
]


def _nhl_games() -> list[Game]:
    games = []
    for index, (home, home_abbrev, away, away_abbrev) in enumerate(TEAMS):
        games.append(
            Game(
                game_id=f"g{index}",
                game_date="2026-10-22",
                start_time_utc=f"2026-10-22T{23 - index:02d}:00:00Z",
                game_state="FUT",
                away=TeamSide(team_id=10 + index, abbrev=away_abbrev, name=away),
                home=TeamSide(team_id=index, abbrev=home_abbrev, name=home),
            )
        )
    return games


def _prop_row(game_index: int, player: str, side: str, odds: int, line: float = 3.5, book: str = "draftkings") -> dict:
    home, _h, away, _a = TEAMS[game_index]
    return {
        "sportsbook": book,
        "event_id": f"ev{game_index}",
        "home_team": home,
        "away_team": away,
        "market_type": "player_shots_on_goal",
        "player_name": player,
        "selection_type": side,
        "odds_american": odds,
        "line": line,
        "is_main_line": True,
    }


def test_duplicate_sharp_rows_count_once():
    rows = [
        _prop_row(0, "Macklin Celebrini", "over", 120),
        _prop_row(0, "Macklin Celebrini", "over", 120),
        _prop_row(0, "Macklin Celebrini", "under", -150),
    ]
    offers = parse_sharp_rows(rows, _nhl_games(), [], "NHL")
    assert sorted(offer.side for offer in offers) == ["OVER", "UNDER"]


def test_invalid_sharp_prices_are_dropped():
    rows = [_prop_row(0, "Macklin Celebrini", "over", -90), _prop_row(0, "Macklin Celebrini", "under", -150)]
    offers = parse_sharp_rows(rows, _nhl_games(), [], "NHL")
    assert [offer.side for offer in offers] == ["UNDER"]


class _FakeSharp:
    """Serves one moneyline row per game and props pages of PAGE rows keyed by cursor."""

    PAGE = 3

    def __init__(self, props_per_game: int = 10, always_more: bool = False, throttle_first: bool = False):
        self.calls: list[dict] = []
        self.props_per_game = props_per_game
        self.always_more = always_more
        self.throttle_first = throttle_first
        self.throttled = False

    def __call__(self, url, params=None, *, ttl_seconds=300, use_cache=True, header_sink=None, headers=None, attempts=None):
        params = dict(params or {})
        self.calls.append(params)
        if self.throttle_first and not self.throttled and params["market"] == "props":
            self.throttled = True
            request = httpx.Request("GET", url)
            response = httpx.Response(429, headers={"retry-after": "7"}, request=request)
            raise httpx.HTTPStatusError("slow down", request=request, response=response)
        if params["market"] != "props":
            data = []
            for index, (home, _h, away, _a) in enumerate(TEAMS):
                data.append(
                    {
                        "sportsbook": "draftkings",
                        "event_id": f"ev{index}",
                        "home_team": home,
                        "away_team": away,
                        "market_type": "moneyline",
                        "selection_type": "home",
                        "odds_american": -120,
                        "is_main_line": True,
                    }
                )
            return {"data": data, "pagination": {"has_more": False}}
        events = params["event_id"].split(",")
        rows = []
        for event in events:
            index = int(event[2:])
            for number in range(self.props_per_game):
                rows.append(_prop_row(index, f"Player {index}-{number}", "over", 110, line=2.5))
        start = int(params["cursor"][1:]) if params.get("cursor") else 0
        page = rows[start : start + self.PAGE]
        more = self.always_more or start + self.PAGE < len(rows)
        pagination = {"has_more": more}
        if more:
            pagination["next_cursor"] = f"c{start + self.PAGE}"
        return {"data": page, "pagination": pagination}


def _patch_sharp(monkeypatch, fake):
    sleeps: list[float] = []
    monkeypatch.setattr(sharp_client, "get_json", fake)
    monkeypatch.setattr(sharp_client, "_sleep", sleeps.append)
    return sleeps


def test_props_page_through_every_game_past_eight_pages(monkeypatch):
    fake = _FakeSharp(props_per_game=10)
    _patch_sharp(monkeypatch, fake)
    offers = sharp_client.fetch_sharp_offers(_nhl_games(), [], "key", "NHL")
    props = [offer for offer in offers if offer.market_type == "SOG"]
    assert {offer.game_id for offer in props} == {"g0", "g1", "g2", "g3"}
    assert len(props) == 40
    prop_calls = [call for call in fake.calls if call["market"] == "props"]
    assert len(prop_calls) > 8
    assert all(call["limit"] == 200 for call in prop_calls)
    # Earliest game first (g3 starts first here), three events to a request.
    assert prop_calls[0]["event_id"].split(",")[0] == "ev3"
    assert max(len(call["event_id"].split(",")) for call in prop_calls) == 3


def test_props_stop_at_the_page_safety_limit(monkeypatch):
    fake = _FakeSharp(always_more=True)
    _patch_sharp(monkeypatch, fake)
    monkeypatch.setattr(sharp_client, "MAX_PAGES", 6)
    sharp_client.fetch_sharp_offers(_nhl_games(), [], "key", "NHL")
    assert len([call for call in fake.calls if call["market"] == "props"]) == 5


def test_rate_limit_waits_for_the_reset_instead_of_hammering(monkeypatch):
    fake = _FakeSharp(props_per_game=2, throttle_first=True)
    sleeps = _patch_sharp(monkeypatch, fake)
    offers = sharp_client.fetch_sharp_offers(_nhl_games(), [], "key", "NHL")
    assert sleeps and sleeps[0] >= 7
    assert len([offer for offer in offers if offer.market_type == "SOG"]) == 8
