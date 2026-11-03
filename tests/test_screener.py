"""OmniScreener engine rules, scan budgeting, and the page (offline, no network)."""

import os
from dataclasses import replace
from pathlib import Path

os.environ["PUCKEDGE_DISABLE_SCHEDULER"] = "1"
os.environ["PUCKEDGE_LIVE"] = "0"

import pytest

from src.api.data_models import Game, TeamSide
from src.api.sharp_client import parse_sharp_rows
from src.engine import screener as sc
from src.engine.devig import american_to_implied, decimal_to_american
from src.stats import scan


def q(
    *,
    sport="NHL",
    game="g1",
    market="SOG",
    entity="Connor McDavid",
    side="OVER",
    line=2.5,
    american=-145,
    book="draftkings",
    matchup="EDM @ FLA",
    team="",
) -> sc.Quote:
    return sc.Quote(sport, game, matchup, "2026-10-30T23:00:00Z", market, entity, team, side, line, american, book)


def series_for(values, sport="NHL", name="Connor McDavid", key="shots"):
    return {(sport, sc.norm_name(name)): sc.PlayerSeries(sport=sport, name=name, stats={key: list(values)})}


def row_with(american, hits, games=10, line=2.5, side="OVER", book="draftkings"):
    """One scanned row whose last `games` games hit `hits` times."""
    values = [3.0] * hits + [1.0] * (games - hits) if side == "OVER" else [1.0] * hits + [4.0] * (games - hits)
    rows = sc.build_rows([q(american=american, line=line, side=side, book=book)], None, series_for(values))
    assert len(rows) == 1
    return rows[0]


# ---------------------------------------------------------------- rows, prices, EV


def test_two_books_merge_into_one_row_with_best_real_price():
    rows = sc.build_rows([q(american=-150, book="draftkings"), q(american=-140, book="fanduel")])
    assert len(rows) == 1
    row = rows[0]
    assert row.price == -140 and row.book == "fanduel"
    assert row.books == {"draftkings": -150, "fanduel": -140}
    assert not row.est and row.selectable


def test_synthetic_price_never_wins_best_odds_and_est_rows_are_inert():
    rows = sc.build_rows([q(american=-150, book="draftkings"), q(american=+300, book="synthetic-bet365")])
    assert rows[0].price == -150 and rows[0].book == "draftkings"
    est_only = sc.build_rows([q(american=-200, book="synthetic-bet365")], None, series_for([3] * 10))
    row = est_only[0]
    assert row.est and not row.selectable and row.ev is None and row.flags == () and row.hit is None
    assert sc.deck_anvil(est_only) == [] and sc.deck_hot_odds(est_only) == [] and sc.deck_streaks(est_only) == []


def test_ev_formula_and_plus_ev_flag():
    # DK posts both sides at -110 so the no-vig chance is 50%; FD pays +120 on the over.
    rows = sc.build_rows([q(american=-110), q(side="UNDER", american=-110), q(american=+120, book="fanduel")])
    over = next(row for row in rows if row.side == "OVER")
    assert over.price == 120 and over.win_src == "market"
    assert over.win_prob == pytest.approx(0.5)
    assert over.ev == pytest.approx(0.5 * 1.2 - 0.5)
    assert over.ev_pct == pytest.approx(10.0) and over.has_flag("plus_ev")
    under = next(row for row in rows if row.side == "UNDER")
    assert under.ev < 0 and not under.has_flag("plus_ev")


def test_model_probability_beats_the_market_when_the_board_has_one():
    quote = q(american=+150)
    key = sc.row_key(quote)
    row = sc.build_rows([quote], {key: 0.55})[0]
    assert row.win_src == "model" and row.win_prob == pytest.approx(0.55)
    assert row.ev == pytest.approx(0.55 * 1.5 - 0.45)


def test_labels_and_milestones():
    over = sc.build_rows([q()])[0]
    assert over.selection == "Connor McDavid Over 2.5 SOG" and over.milestone == "3+"
    under = sc.build_rows([q(side="UNDER")])[0]
    assert under.selection == "Connor McDavid Under 2.5 SOG" and under.milestone == ""
    goal = sc.build_rows([q(market="GOALSCORER", side="YES", line=0.5, american=+120)])[0]
    assert goal.selection == "Connor McDavid Anytime Goal" and goal.side == "YES"


def test_best_price_and_delta_use_the_same_real_price():
    row = row_with(-145, 9)
    assert row.l10_rate == pytest.approx(0.9)
    assert row.delta_pp == pytest.approx((0.9 - american_to_implied(-145)) * 100)


# ---------------------------------------------------------------- decks


@pytest.mark.parametrize("price,expected", [(-400, True), (-125, True), (-124, False), (-401, False), (-250, True)])
def test_anvil_price_bounds_are_inclusive(price, expected):
    row = row_with(price, 9)
    assert (row in sc.deck_anvil([row])) is expected


def test_anvil_hit_rate_bounds():
    ok = row_with(-150, 8)
    assert sc.deck_anvil([ok]) == [ok]
    low = row_with(-150, 7)
    assert sc.deck_anvil([low]) == []
    almost = replace(ok, hit=replace(ok.hit, l10_hits=799, l10_games=1000))  # 79.9%
    assert sc.deck_anvil([almost]) == []
    exactly = replace(ok, hit=replace(ok.hit, l10_hits=800, l10_games=1000))  # 80.0%
    assert sc.deck_anvil([exactly]) == [exactly]


def test_anvil_sorts_by_win_probability_then_hit_rate():
    a = replace(row_with(-150, 8), win_prob=0.70)
    b = replace(row_with(-200, 9), win_prob=0.80)
    c = replace(row_with(-300, 10), win_prob=0.80)
    ordered = sc.deck_anvil([a, b, c])
    assert ordered == [c, b, a]  # b and c tie on win probability, c has the better hit rate


@pytest.mark.parametrize("price,expected", [(100, True), (280, True), (281, False), (99 + 1, True), (-110, False)])
def test_hot_odds_price_bounds(price, expected):
    row = row_with(price, 7)
    assert (row in sc.deck_hot_odds([row])) is expected


def test_hot_odds_hit_rate_bounds_and_sort():
    assert sc.deck_hot_odds([row_with(+150, 6)])
    assert not sc.deck_hot_odds([row_with(+150, 5)])
    best = replace(row_with(+150, 7), ev=0.20)
    worse = replace(row_with(+160, 9), ev=0.05)
    assert sc.deck_hot_odds([worse, best])[0] == best


def test_streaks_need_every_game_and_a_minimum_sample():
    perfect = row_with(-150, 10)
    assert sc.streak_info(perfect)[1].startswith("10/10 Games")
    assert sc.deck_streaks([perfect]) == [perfect]
    nine_of_ten = row_with(-150, 9)
    assert sc.deck_streaks([nine_of_ten]) == []
    short = sc.build_rows([q()], None, series_for([3, 3, 3, 3]))[0]  # 4 of 4 is too small
    assert sc.deck_streaks([short]) == []
    seven = sc.build_rows([q()], None, series_for([3] * 7))[0]
    assert sc.streak_info(seven)[1] == "7/7 Games"
    # season is 12 games, L10 perfect but season is not: L10 badge
    mixed = sc.build_rows([q()], None, series_for([3] * 10 + [1, 1]))[0]
    assert sc.streak_info(mixed)[1].endswith("(L10)")


def test_streak_cushion_is_average_minus_line_and_never_negative_in_the_cell():
    row = sc.build_rows([q(line=1.5)], None, series_for([4, 3, 3, 4, 3, 3]))[0]
    assert row.cushion_l10 == pytest.approx(sum([4, 3, 3, 4, 3, 3]) / 6 - 1.5)
    under = sc.build_rows([q(side="UNDER", line=2.5)], None, series_for([1, 1, 2, 1, 0, 1]))[0]
    assert under.cushion_l10 == pytest.approx(2.5 - sum([1, 1, 2, 1, 0, 1]) / 6)
    from src.ui import omniscreener as ui

    losing = sc.build_rows([q(line=4.5)], None, series_for([1] * 6))[0]
    assert losing.cushion_l10 < 0
    assert "—" in ui._cushion_cell(losing) and "-" not in ui._cushion_cell(losing).replace("</", "").replace("om-", "")


def test_under_hit_rate_counts_games_below_the_line():
    values = [1, 2, 3, 3, 1, 2, 4, 1, 2, 2]  # below 2.5: 1,2,1,2,1,2,2 = 7
    row = sc.build_rows([q(side="UNDER", line=2.5)], None, series_for(values))[0]
    assert row.hit.l10_hits == 7
    over = sc.build_rows([q(side="OVER", line=2.5)], None, series_for(values))[0]
    assert over.hit.l10_hits == 3


def test_unscanned_rows_show_dash_not_zero():
    from src.ui import omniscreener as ui

    row = sc.build_rows([q()])[0]
    assert row.l10_rate is None and not row.scanned
    cell = ui._hit_cell(row)
    assert "—" in cell and "0%" not in cell and "0/" not in cell


def test_hit_slider_hides_unscanned_rows_only_above_the_minimum():
    scanned = row_with(-150, 9)
    blank = sc.build_rows([q(entity="Other Player")])[0]
    both = [scanned, blank]
    assert len(sc.filter_rows(both, min_hit=0.5)) == 2
    assert sc.filter_rows(both, min_hit=0.8) == [scanned]
    assert sc.filter_rows(both, min_hit=1.0) == []
    assert sc.filter_rows(both, search="mcdavid") == [scanned]
    assert sc.filter_rows(both, category="Rebounds") == []


# ---------------------------------------------------------------- odds range and combined odds


def test_odds_range_is_monotone_across_the_even_gap():
    assert sc.in_odds_range(-110, -125, 100)
    assert sc.in_odds_range(100, 100, 300) and sc.in_odds_range(300, 100, 300)
    assert not sc.in_odds_range(-110, 100, 300)  # -110 is below +100 on the real scale
    assert not sc.in_odds_range(301, 100, 300)
    assert sc.in_odds_range(-400, -400, -125) and sc.in_odds_range(-125, -400, -125)
    assert not sc.in_odds_range(-124, -400, -125) and not sc.in_odds_range(-401, -400, -125)
    assert sc.in_odds_range(-5000, None, None)
    assert not sc.in_odds_range(50, None, None)  # not a real American price


def test_combined_odds_multiplies_decimals_and_never_goes_negative():
    rows = [row_with(100, 7), row_with(100, 7), row_with(100, 7)]
    american, decimal = sc.combined_odds(rows)
    assert decimal == pytest.approx(8.0) and american == 700
    two = sc.combined_odds([row_with(-110, 9), row_with(-110, 9)])
    assert two[1] == pytest.approx((1 + 100 / 110) ** 2) and two[0] == decimal_to_american(two[1])
    empty = sc.combined_odds([])
    assert empty[1] == 1.0


# ---------------------------------------------------------------- line shop


def test_cents_ignore_the_even_gap():
    assert sc.cents_gap(+105, -110) == pytest.approx(15)
    assert sc.cents_gap(-105, -130) == pytest.approx(25)
    assert sc.cents_gap(+130, +105) == pytest.approx(25)
    assert sc.cents_gap(-130, -105) == pytest.approx(-25)


def _shop(books: dict[str, tuple[float, int]], side="OVER"):
    quotes = [q(side=side, line=line, american=price, book=book) for book, (line, price) in books.items()]
    return sc.deck_line_shop(sc.build_rows(quotes))


def test_line_shop_needs_two_real_books():
    assert _shop({"draftkings": (2.5, -130)}) == []
    only_synthetic = sc.deck_line_shop(sc.build_rows([q(book="draftkings"), q(book="synthetic-bet365", american=+500)]))
    assert only_synthetic == []


def test_line_shop_flags_at_exactly_25_cents_and_not_at_24():
    flagged = _shop({"draftkings": (2.5, -130), "fanduel": (2.5, -105)})
    assert len(flagged) == 1 and flagged[0].flagged and flagged[0].soft_book == "fanduel"
    quiet = _shop({"draftkings": (2.5, -130), "fanduel": (2.5, -106)})
    assert len(quiet) == 1 and not quiet[0].flagged
    # symmetric: the other book is the soft one
    other = _shop({"draftkings": (2.5, +130), "fanduel": (2.5, +105)})
    assert other[0].flagged and other[0].soft_book == "draftkings"
    # across the even gap: +105 vs -120 is 25 cents
    gap = _shop({"draftkings": (2.5, -120), "fanduel": (2.5, +105)})
    assert gap[0].price_cents == pytest.approx(25) and gap[0].flagged


def test_line_shop_flags_a_full_stat_unit_on_the_same_side():
    over = _shop({"draftkings": (2.5, -115), "fanduel": (3.5, -115)})
    assert over[0].flagged and over[0].unit_gap == pytest.approx(1.0) and over[0].soft_book == "draftkings"
    under = _shop({"draftkings": (2.5, -115), "fanduel": (3.5, -115)}, side="UNDER")
    assert under[0].flagged and under[0].soft_book == "fanduel"
    half = _shop({"draftkings": (2.5, -115), "fanduel": (3.0, -115)})
    assert not half[0].flagged


def test_line_shop_marks_soft_book_on_the_row_badges():
    rows = sc.build_rows([q(american=-130, book="draftkings"), q(american=-105, book="fanduel")])
    assert rows[0].has_flag("soft_book") and "cents" in rows[0].soft_note


# ---------------------------------------------------------------- slip conflicts


def test_contradictory_selection_is_blocked_and_adds_nothing():
    over = sc.build_rows([q(side="OVER"), q(side="UNDER", american=-110)])
    by_side = {row.side: row for row in over}
    problem = sc.selection_conflict([by_side["OVER"], by_side["UNDER"]], [])
    assert problem and "fights" in problem
    other = sc.build_rows([q(entity="Leon Draisaitl")])[0]
    assert sc.selection_conflict([by_side["OVER"], other], []) is None
    # against a leg already on the slip
    assert sc.selection_conflict([by_side["UNDER"]], [by_side["OVER"].slip_quote()])
    # the same leg twice is not a conflict
    assert sc.selection_conflict([by_side["OVER"]], [by_side["OVER"].slip_quote()]) is None


def test_slip_quote_has_the_studio_shape():
    row = row_with(-150, 9)
    quote = row.slip_quote()
    for key in ("id", "sport", "player", "selection", "probability", "american", "decimal", "book", "matchup", "game_id", "game_key", "market_type", "pick", "line"):
        assert key in quote
    assert quote["pick"] == "OVER" and quote["market_type"] == "SOG" and quote["game_key"] == "NHL|EDM @ FLA"
    assert quote["probability"] > 0 and quote["decimal"] > 1
    leg = row.as_leg()
    assert leg.american_odds == -150 and leg.sport == "NHL"


# ---------------------------------------------------------------- scan budget and parsing


def test_scan_targets_respect_budget_and_skip_known_players():
    quotes = [q(entity=f"Player {i}", american=-150 if i % 2 else +900) for i in range(60)]
    rows = sc.build_rows(quotes)
    targets = sc.scan_targets(rows, {}, {}, limit=25)
    assert len(targets) == 25
    # players priced inside the Anvil band come first
    assert all(int(name.split()[1]) % 2 == 1 for _sport, name in targets)
    known = {("NHL", sc.norm_name("Player 1")): None}
    assert ("NHL", "Player 1") not in sc.scan_targets(rows, known, {}, limit=100)
    tried = {("NHL", sc.norm_name("Player 3")): 2}
    assert ("NHL", "Player 3") not in sc.scan_targets(rows, {}, tried, limit=100)


def test_scan_batch_uses_the_budget_and_stops_on_a_rate_limit():
    calls = []

    def fetch(sport, name):
        calls.append(name)
        return sc.PlayerSeries(sport=sport, name=name, stats={"shots": [3.0] * 6})

    targets = [("NHL", f"P{i}") for i in range(40)]
    results, errors, status = scan.scan_batch(targets, budget=25, fetch=fetch)
    assert len(results) == 25 and len(calls) == 25 and status == "done" and not errors

    def limited(sport, name):
        raise scan.ScanLimited()

    results, errors, status = scan.scan_batch(targets, budget=25, fetch=limited, workers=1)
    assert status == "limited" and results == {}


def test_scan_batch_counts_failures_for_one_retry():
    def broken(sport, name):
        raise RuntimeError("boom")

    _results, errors, status = scan.scan_batch([("NBA", "A B")], fetch=broken)
    assert errors == {("NBA", sc.norm_name("A B")): 1} and status == "done"


def test_a_namesake_without_a_game_log_is_skipped(monkeypatch):
    import httpx

    search = {"items": [{"displayName": "Jack Hughes", "league": "nhl", "id": "1"}, {"displayName": "Jack Hughes", "league": "nhl", "id": "2"}]}
    log = {"names": ["goals"], "events": {}, "seasonTypes": []}
    asked = []

    def fake_get_json(url, params=None, **kwargs):
        if "search" in url:
            return search
        asked.append(url)
        if "/athletes/1/" in url:
            raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url), response=httpx.Response(404))
        return log

    monkeypatch.setattr(scan, "get_json", fake_get_json)
    monkeypatch.setattr(scan, "series_from_espn", lambda *args, **kwargs: "series")
    assert scan.fetch_player_series("NHL", "Jack Hughes") == "series"
    assert len(asked) == 2 and "/athletes/2/" in asked[1]

    def only_missing(url, params=None, **kwargs):
        if "search" in url:
            return {"items": search["items"][:1]}
        raise httpx.HTTPStatusError("404", request=httpx.Request("GET", url), response=httpx.Response(404))

    monkeypatch.setattr(scan, "get_json", only_missing)
    assert scan.fetch_player_series("NHL", "Jack Hughes") is None  # no log: not an error


def test_espn_gamelog_parsing_skips_dnps_and_orders_by_date():
    payload = {
        "names": ["minutes", "totalRebounds", "assists", "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "points"],
        "events": {"1": {"gameDate": "2026-10-20T00:00:00Z"}, "2": {"gameDate": "2026-10-22T00:00:00Z"}, "3": {"gameDate": "2026-10-24T00:00:00Z"}},
        "seasonTypes": [
            {"displayName": "2026-27 Preseason", "categories": [{"events": [{"eventId": "9", "stats": ["30", "9", "9", "9-9", "99"]}]}]},
            {
                "displayName": "2026-27 Regular Season",
                "categories": [
                    {"events": [{"eventId": "1", "stats": ["30", "5", "2", "3-8", "20"]}, {"eventId": "3", "stats": ["0", "0", "0", "0-0", "0"]}]},
                    {"events": [{"eventId": "2", "stats": ["33", "7", "4", "1-4", "25"]}]},
                ],
            },
        ],
    }
    series = scan.series_from_espn("NBA", "Test Guy", payload, team="DEN")
    assert series.stats["points"] == [25.0, 20.0]  # newest first, the 0-minute game is dropped
    assert series.stats["threes"] == [1.0, 3.0] and series.stats["rebounds"] == [7.0, 5.0]
    spec = sc.spec_for("NBA", "PRA")
    assert sc.series_values(series, spec) == [36.0, 27.0]


def test_goalie_saves_come_from_shots_against_minus_goals_against():
    payload = {
        "names": ["timeOnIcePerGame", "goalsAgainst", "shotsAgainst", "saves"],
        "events": {"1": {"gameDate": "2026-10-20"}},
        "seasonTypes": [{"displayName": "2026-27 Regular Season", "categories": [{"events": [{"eventId": "1", "stats": ["60:00", "1", "18", "17"]}]}]}],
    }
    series = scan.series_from_espn("NHL", "Goalie G", payload)
    assert series.stats["saves"] == [17.0]


def test_double_double_and_combo_values():
    series = sc.PlayerSeries(
        "NBA", "Big Man", stats={"points": [22, 8], "rebounds": [11, 9], "assists": [3, 2], "steals": [1, 0], "blocks": [0, 1]}
    )
    assert sc.series_values(series, sc.spec_for("NBA", "DOUBLE_DOUBLE")) == [1.0, 0.0]
    assert sc.series_values(series, sc.spec_for("NBA", "STOCKS")) == [1.0, 1.0]
    assert sc.series_values(series, sc.spec_for("NHL", "BLOCKS")) is None  # no free game log column


# ---------------------------------------------------------------- provider mapping


def _game():
    return Game(
        game_id="g1",
        game_date="2026-10-30",
        start_time_utc="2026-10-30T23:00:00Z",
        game_state="FUT",
        away=TeamSide(team_id=1, abbrev="EDM", name="Edmonton Oilers"),
        home=TeamSide(team_id=2, abbrev="FLA", name="Florida Panthers"),
    )


def _prop(market, selection_type, line, american=-120, selection="Over", player="Connor McDavid", book="draftkings"):
    return {
        "sportsbook": book,
        "event_id": "ev1",
        "home_team": "Florida Panthers",
        "away_team": "Edmonton Oilers",
        "market_type": market,
        "selection": selection,
        "selection_type": selection_type,
        "odds_american": american,
        "line": line,
        "player_name": player,
        "is_main_line": True,
    }


def test_studio_parsing_is_unchanged_but_extended_reads_real_extra_markets():
    rows = [
        _prop("player_goals", "over", 0.5, +180),
        _prop("player_goals", "over", 1.5, +900),
        _prop("player_made_threes", "over", 2.5),
        _prop("player_double_double", "other", None, +400, "Yes"),
    ]
    assert parse_sharp_rows(rows, [_game()], [], "NHL") == []
    offers = parse_sharp_rows(rows, [_game()], [], "NHL", extended=True)
    kinds = sorted((offer.market_type, offer.side, offer.line) for offer in offers)
    assert ("GOALSCORER", "YES", 0.5) in kinds
    assert ("GOALS", "OVER", 1.5) in kinds
    assert ("THREES", "OVER", 2.5) in kinds
    assert ("DOUBLE_DOUBLE", "YES", 0.5) in kinds
    quote = sc.quote_from_offer("NBA", next(offer for offer in offers if offer.market_type == "DOUBLE_DOUBLE"))
    assert quote is not None and quote.side == "YES"


# ---------------------------------------------------------------- the page (AppTest)

from streamlit.testing.v1 import AppTest  # noqa: E402

from src.services.screener_feed import SportFeed  # noqa: E402
from src.ui.omniscreener import NAV_OMNI, NAV_STUDIO  # noqa: E402

APP_FILE = Path(__file__).resolve().parents[1] / "app.py"


def _fake_feed(board, sport, *, sharp_key="", live=True):
    if sport != "NHL":
        return SportFeed(sport=sport, games=0)
    quotes = [
        q(american=-150, book="draftkings", line=1.5),
        q(american=-140, book="fanduel", line=1.5),
        q(side="UNDER", american=+110, book="draftkings", line=1.5),
        q(entity="Leon Draisaitl", market="POINTS", line=0.5, american=-180, book="draftkings"),
        q(entity="Leon Draisaitl", market="POINTS", line=0.5, american=-200, book="fanduel"),
        q(entity="Sam Reinhart", market="SOG", line=2.5, american=+150, book="draftkings"),
    ]
    return SportFeed(sport="NHL", quotes=quotes, games=1, source="draftkings")


def _fake_fetch(sport, name):
    base = {"Connor McDavid": [4.0] * 10, "Leon Draisaitl": [1.0, 2.0] * 5, "Sam Reinhart": [3.0, 3.0, 3.0, 1.0, 3.0, 3.0, 1.0, 3.0, 3.0, 3.0]}[name]
    stats = {"shots": base, "points": base}
    return sc.PlayerSeries(sport=sport, name=name, team="EDM", stats=stats)


def _app(tmp_path, monkeypatch):
    monkeypatch.setenv("PUCKEDGE_DB", str(tmp_path / "ui.db"))
    monkeypatch.setenv("PUCKEDGE_LIVE", "0")
    monkeypatch.setenv("PUCKEDGE_DISABLE_SCHEDULER", "1")
    monkeypatch.setenv("PUCKEDGE_DISABLE_UPDATES", "1")
    monkeypatch.setattr("src.ui.omniscreener.build_sport_feed", _fake_feed)
    monkeypatch.setattr(scan, "fetch_player_series", _fake_fetch)
    app = AppTest.from_file(str(APP_FILE), default_timeout=90)
    app.session_state["account"] = {"name": "Test User", "username": "tester", "ips": ["127.0.0.1"]}
    app.session_state["owner_override"] = True
    app.session_state["limits_skipped"] = True
    app.session_state["nav_page"] = NAV_OMNI
    return app


def _texts(app):
    return " ".join(block.value for block in app.markdown)


def test_omniscreener_page_renders_with_footer_and_scanned_rows(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    app.run()
    assert not app.exception, app.exception
    text = _texts(app)
    assert "Created and Owned by Harvir Vineypal" in text
    assert "Connor McDavid" in text  # Anvil: 10/10 shots over 1.5 at -140
    assert "10/10" in text
    assert "Scanned 4 of 4 props" in text  # McDavid over+under, Draisaitl, Reinhart rows
    assert "Sam Reinhart" not in text  # +150 is not in the Anvil band
    labels = [button.label for button in app.button]
    assert "+ Add to Slip" in labels


def test_slip_persists_across_a_page_switch_and_a_conflict_is_blocked(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    app.run()
    mcdavid = next(row for row in app.session_state["omni_rows"][1] if row.entity == "Connor McDavid" and row.side == "OVER")
    app.button(key=f"omni-add-{mcdavid.id}").click()
    app.run()
    assert not app.exception, app.exception
    slip = app.session_state["slip"]
    assert len(slip) == 1 and slip[0]["player"] == "Connor McDavid" and slip[0]["book"] == "fanduel"
    app.session_state["nav_page"] = NAV_STUDIO
    app.run()
    assert not app.exception, app.exception
    assert len(app.session_state["slip"]) == 1
    assert "Connor McDavid" in _texts(app) + " ".join(caption.value for caption in app.caption)
    # back on OmniScreener the same leg shows as already in the slip and nothing else changed
    app.session_state["nav_page"] = NAV_OMNI
    app.run()
    assert not app.exception, app.exception
    assert any(button.label == "✓ In Slip" for button in app.button)
    assert len(app.session_state["slip"]) == 1


def test_drawer_appears_for_two_checked_props_and_blocks_contradictions(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    app.session_state["omni_deck"] = "Full Feed"
    app.run()
    assert not app.exception, app.exception
    boxes = [box for box in app.checkbox if box.key and box.key.startswith("omni-pick-")]
    assert len(boxes) >= 3
    assert not any("Legs Selected" in block.value for block in app.markdown)
    # find the McDavid over and under checkboxes through the rows
    rows = {row.id: row for row in app.session_state["omni_rows"][1]}
    over = next(row for row in rows.values() if row.entity == "Connor McDavid" and row.side == "OVER")
    under = next(row for row in rows.values() if row.entity == "Connor McDavid" and row.side == "UNDER")
    other = next(row for row in rows.values() if row.entity == "Leon Draisaitl")
    app.checkbox(key=f"omni-pick-{over.id}").check()
    app.run()
    app.checkbox(key=f"omni-pick-{other.id}").check()
    app.run()
    assert any("2 Legs Selected" in block.value and "Combined Odds" in block.value for block in app.markdown)
    app.button(key="omni_bundle").click()
    app.run()
    assert not app.exception, app.exception
    assert len(app.session_state["slip"]) == 2
    assert app.session_state["omni_selected"] == {}
    # contradictory pair on a fresh slip
    app.session_state["slip"] = []
    app.checkbox(key=f"omni-pick-{over.id}").check()
    app.run()
    app.checkbox(key=f"omni-pick-{under.id}").check()
    app.run()
    app.button(key="omni_bundle").click()
    app.run()
    assert not app.exception, app.exception
    assert app.session_state["slip"] == []
    assert any("fights" in warning.value or "Nothing was added" in warning.value for warning in app.warning)


def test_line_shop_deck_shows_na_for_books_not_on_the_plan(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    app.session_state["omni_deck"] = "Line-Shop Radar"
    app.run()
    assert not app.exception, app.exception
    text = _texts(app)
    assert "n/a (not on your plan)" in text
    assert "Connor McDavid" in text
    assert "Bet365" in text and "Pinnacle" in text


def test_vault_and_setup_navigation_still_work(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch)
    app.session_state["nav_page"] = "Setup"
    app.run()
    assert not app.exception, app.exception
    app.session_state["nav_page"] = "📊 Vault & Ledger"
    app.run()
    assert not app.exception, app.exception
