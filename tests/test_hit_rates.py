"""100% Hit Rates scanner: ladder, windows, traps, price matching, SGP and the card. No network."""

from __future__ import annotations

import os

os.environ["PUCKEDGE_DISABLE_SCHEDULER"] = "1"

import pytest
from streamlit.testing.v1 import AppTest

from src.api.data_models import MarketLeg
from src.engine.hit_rates import (
    BLOWOUT_SPREAD,
    HIGH_CUSHION,
    GameData,
    PlayerSeries,
    best_full_rung,
    blowout_trap,
    build_sgp,
    count_hits,
    defense_trap,
    display_rows,
    evaluate_game,
    ladder,
    match_price,
    index_offers,
    missed_by_one,
    rank_table,
    scan_series,
    window_values,
)

POINTS = (10, 15, 20)


def _decimal(american: int) -> float:
    return 1 + american / 100 if american > 0 else 1 + 100 / -american


def _leg(player: str, market: str, line: float, american: int = -200, *, game: str = "g1", sport: str = "NBA", book: str = "draftkings", side: str = "OVER", team: str = "AWY") -> MarketLeg:
    decimal = _decimal(american)
    return MarketLeg(
        leg_id=f"{game}|{player}|{market}|{line}|{side}|{book}",
        game_id=game,
        matchup="AWY @ HOM",
        game_date="2026-10-30",
        start_time_utc="2026-10-30T23:00:00Z",
        market_type=market,
        market_key=f"{game}|{market}|{player}|{line}",
        target_entity=player,
        team_abbrev=team,
        selection=f"{player} {side} {line}",
        side=side,
        line=line,
        american_odds=american,
        decimal_odds=decimal,
        implied_prob=1 / decimal,
        fair_prob=0.9 / decimal,
        model_prob=0.5,
        ev=0.0,
        bookmaker=book,
        rationale="",
        sport=sport,
    )


def _series(name: str, stats: dict[str, list[float]], *, sport: str = "NBA", team: str = "AWY", opponent: str = "HOM") -> PlayerSeries:
    return PlayerSeries(name=name, sport=sport, team=team, opponent=opponent, stats=stats)


def _game(series: list[PlayerSeries], sport: str = "NBA", **extra) -> GameData:
    return GameData(sport=sport, game_id="g1", away="AWY", home="HOM", series=series, **extra)


# --------------------------------------------------------------------------- ladder and windows


def test_ladder_picks_the_highest_threshold_that_hit_every_game():
    assert best_full_rung([22, 21, 20, 25, 30, 20], POINTS, "season") == (20, 6, 6)
    assert best_full_rung([22, 21, 19, 25, 30, 20], POINTS, "season") == (15, 6, 6)
    assert best_full_rung([22, 21, 12, 25, 30, 20], POINTS, "season") == (10, 6, 6)
    assert best_full_rung([22, 21, 9, 25, 30, 20], POINTS, "season") is None


def test_a_tie_with_the_line_counts_as_a_hit():
    assert count_hits([10, 10, 12], 10) == 3
    assert count_hits([9.5, 10], 10) == 1


def test_ladder_lists_every_rung_highest_first_with_hits_and_games():
    assert ladder([25, 16, 12, 11, 30], POINTS, "season") == [(20, 2, 5), (15, 3, 5), (10, 5, 5)]


def test_season_l10_and_l5_windows_look_at_different_games():
    values = [25] * 5 + [16] * 5 + [8] * 2  # newest first, 12 games
    assert best_full_rung(values, POINTS, "season") is None
    assert best_full_rung(values, POINTS, "L10") == (15, 10, 10)
    assert best_full_rung(values, POINTS, "L5") == (20, 5, 5)


def test_minimum_sample_guards():
    assert window_values([20] * 4, "season") is None
    assert window_values([20] * 5, "season") == [20] * 5
    assert window_values([20] * 9, "L10") is None
    assert len(window_values([20] * 14, "L10")) == 10
    assert window_values([20] * 4, "L5") is None
    assert len(window_values([20] * 14, "L5")) == 5
    assert window_values([], "season") is None
    assert best_full_rung([20] * 4, POINTS, "season") is None


def test_season_scan_reports_hits_equal_games_and_a_percent_of_100():
    rows = scan_series(_series("A", {"POINTS": [22, 21, 20, 25, 30, 20]}), "season")
    top = max(rows, key=lambda row: row.line)
    assert (top.line, top.hits, top.games) == (20, 6, 6)
    assert top.hit_pct == 1.0 and top.sample_text == "6/6 Games (100%)" and top.streak == "6/6"
    assert all(row.hits <= row.games and row.cushion >= 0 for row in rows)


# --------------------------------------------------------------------------- cushion


def test_cushion_is_average_minus_line_and_never_negative():
    row = max(scan_series(_series("A", {"POINTS": [11, 12, 11, 12, 11, 12]}), "season"), key=lambda r: r.line)
    assert row.line == 10 and row.average == pytest.approx(11.5) and row.cushion == pytest.approx(1.5)


def test_high_cushion_starts_exactly_at_one_and_a_half():
    exactly = max(scan_series(_series("A", {"POINTS": [11, 12, 11, 12, 11, 12]}), "season"), key=lambda r: r.line)
    just_under = max(scan_series(_series("B", {"POINTS": [11, 11, 12, 11, 11, 12]}), "season"), key=lambda r: r.line)
    assert HIGH_CUSHION == 1.5
    assert exactly.cushion == pytest.approx(1.5) and exactly.high_cushion is True
    assert just_under.cushion < 1.5 and just_under.high_cushion is False


# --------------------------------------------------------------------------- missed by one


def test_missed_by_one_needs_nine_of_ten_with_the_miss_exactly_one_short():
    nine_ten = [12, 14, 9, 11, 13, 10, 12, 15, 11, 10]  # one 9 against a 10 line
    assert missed_by_one(nine_ten, 10) == 9
    two_short = [12, 14, 8, 11, 13, 10, 12, 15, 11, 10]
    assert missed_by_one(two_short, 10) is None
    assert missed_by_one([12] * 10, 10) is None  # 10 of 10 is not a miss
    two_misses = [12, 9, 9, 11, 13, 10, 12, 15, 11, 10]
    assert missed_by_one(two_misses, 10) is None
    assert missed_by_one([12, 14, 9, 11, 13, 10, 12, 15, 11], 10) is None  # fewer than 10 games


def test_missed_by_one_rows_come_only_from_the_toggle():
    values = [12, 14, 9, 11, 13, 10, 12, 15, 11, 10]
    series = _series("A", {"POINTS": values})
    assert not any(row.missed_by_one for row in scan_series(series, "L10"))
    rows = scan_series(series, "L10", missed=True)
    assert [(row.line, row.hits, row.games, row.miss_value) for row in rows] == [(10, 9, 10, 9)]
    assert rows[0].missed_by_one and rows[0].sample_text == "9/10 Games (90%)"


# --------------------------------------------------------------------------- traps


def test_top_three_defence_is_a_trap_but_fourth_is_not():
    table = {"POINTS": rank_table({"A": 100.0, "B": 101.0, "C": 102.0, "D": 103.0, "E": 110.0}, verb="allows", noun="points")}
    assert defense_trap(table, "POINTS", "C") is not None  # 3rd fewest allowed
    assert defense_trap(table, "POINTS", "D") is None
    assert "3rd fewest" in defense_trap(table, "POINTS", "C")
    assert defense_trap(table, "POINTS", "ZZZ") is None  # unknown team: no guess
    assert defense_trap(None, "POINTS", "A") is None


def test_spread_of_twelve_and_a_half_is_not_a_blowout_but_twelve_six_is():
    assert BLOWOUT_SPREAD == 12.5
    assert blowout_trap(12.5) is None
    assert blowout_trap(-12.5) is None
    assert blowout_trap(12.6) is not None
    assert blowout_trap(-12.6) is not None
    assert blowout_trap(None) is None


def _trap_rows(spread_line: float | None, table=None):
    legs = [] if spread_line is None else [_leg("HOM", "SPREAD", spread_line, -110, team="HOM")]
    data = _game([_series("A", {"POINTS": [22, 21, 20, 25, 30, 20]})], defense=table or {})
    rows = evaluate_game(data, legs, scope="season")
    return [row for row in rows if row.line == 20]


def test_board_spread_tags_the_prop_and_estimates_are_ignored():
    assert _trap_rows(-12.5)[0].traps == ()
    assert len(_trap_rows(-12.6)[0].traps) == 1
    assert _trap_rows(None)[0].traps == ()
    synthetic = _leg("HOM", "SPREAD", -20.5, -110, team="HOM", book="synthetic-bet365")
    data = _game([_series("A", {"POINTS": [22, 21, 20, 25, 30, 20]})])
    assert all(not row.traps for row in evaluate_game(data, [synthetic], scope="season"))


def test_defence_rank_tags_the_prop_for_that_stat_only():
    table = {"POINTS": rank_table({"HOM": 100.0, "X": 105.0, "Y": 110.0, "Z": 115.0}, verb="allows", noun="points")}
    row = _trap_rows(None, table)[0]
    assert row.is_trap and "fewest points" in row.traps[0]
    assist_table = {"ASSISTS": table["POINTS"]}
    assert _trap_rows(None, assist_table)[0].traps == ()


def test_player_names_match_across_sources():
    from src.engine.hit_rates import norm_name

    assert norm_name("T.J. McConnell") == norm_name("TJ McConnell")
    assert norm_name("James Cook III") == norm_name("James Cook")
    assert norm_name("Alexis Lafrenière") == "alexis lafreniere"
    assert norm_name("Shai Gilgeous-Alexander") == "shai gilgeous alexander"


# --------------------------------------------------------------------------- price matching


def _window(values):
    return list(values)


def test_exact_line_wins_over_a_higher_line():
    legs = [_leg("A", "POINTS", 9.5, -250), _leg("A", "POINTS", 14.5, 120)]
    index = index_offers(legs, "NBA", "g1")
    match = match_price(index, "A", "POINTS", 10, _window([22] * 6))
    assert match.exact and match.line == 9.5 and match.american == -250 and match.verified


def test_nearest_line_above_is_used_and_labelled_when_there_is_no_exact_line():
    legs = [_leg("A", "POINTS", 14.5, 120), _leg("A", "POINTS", 19.5, 300)]
    index = index_offers(legs, "NBA", "g1")
    cleared = match_price(index, "A", "POINTS", 10, _window([22] * 6))
    assert not cleared.exact and cleared.line == 14.5 and cleared.milestone == 15
    assert cleared.verified and "Over 14.5" in cleared.note
    short = match_price(index, "A", "POINTS", 10, _window([12, 22, 22, 22, 22, 22]))
    assert short.line == 14.5 and not short.verified


def test_no_line_posted_returns_none_and_never_invents_a_price():
    index = index_offers([_leg("A", "POINTS", 9.5)], "NBA", "g1")
    assert match_price(index, "B", "POINTS", 10, [22] * 6) is None
    assert match_price(index, "A", "ASSISTS", 4, [6] * 6) is None
    lower_only = index_offers([_leg("A", "POINTS", 9.5)], "NBA", "g1")
    assert match_price(lower_only, "A", "POINTS", 20, [25] * 6) is None
    data = _game([_series("A", {"POINTS": [22] * 6})])
    assert all(row.price is None for row in evaluate_game(data, [], scope="season"))


def test_synthetic_and_under_legs_are_not_prices():
    legs = [_leg("A", "POINTS", 9.5, book="synthetic-bet365"), _leg("A", "POINTS", 9.5, side="UNDER")]
    assert match_price(index_offers(legs, "NBA", "g1"), "A", "POINTS", 10, [22] * 6) is None


def test_the_preferred_book_is_used_when_it_posts_the_line():
    legs = [_leg("A", "POINTS", 9.5, -300, book="fanduel"), _leg("A", "POINTS", 9.5, -150, book="draftkings")]
    index = index_offers(legs, "NBA", "g1")
    assert match_price(index, "A", "POINTS", 10, [22] * 6, "fanduel").book == "fanduel"
    assert match_price(index, "A", "POINTS", 10, [22] * 6, "").book == "draftkings"  # best payout otherwise


def test_a_posted_line_above_the_ladder_becomes_a_scanned_rung():
    data = _game([_series("A", {"POINTS": [32, 31, 30, 35, 40, 30]})])
    rows = evaluate_game(data, [_leg("A", "POINTS", 24.5, -140)], scope="season")
    assert max(row.line for row in rows) == 25
    top = max(rows, key=lambda row: row.line)
    assert top.price.exact and top.price.american == -140


def test_display_rows_keep_one_rung_per_player_stat_and_a_priced_fallback():
    data = _game([_series("A", {"POINTS": [22, 21, 20, 25, 30, 20]})])
    rows = display_rows(evaluate_game(data, [_leg("A", "POINTS", 9.5, -400)], scope="season"))
    assert sorted(row.line for row in rows) == [10, 20]
    assert next(row for row in rows if row.line == 20).price is None
    assert next(row for row in rows if row.line == 10).price.exact


# --------------------------------------------------------------------------- undefeated SGP


def _priced_pool(american: int, players: int = 4):
    stats = {"POINTS": [22, 21, 20, 25, 30, 20], "ASSISTS": [6, 7, 8, 9, 6, 7], "REBOUNDS": [8, 9, 10, 8, 9, 11]}
    series = [_series(f"P{i}", stats) for i in range(players)]
    legs = []
    for i in range(players):
        legs += [_leg(f"P{i}", "POINTS", 19.5, american), _leg(f"P{i}", "ASSISTS", 5.5, american)]
    data = _game(series)
    return [row for row in evaluate_game(data, legs, scope="season") if row.price is not None and row.price.exact]


def test_sgp_lands_inside_the_band_with_three_priced_hundred_percent_props():
    result = build_sgp(_priced_pool(-200))
    assert result.best is not None and len(result.best.props) == 3
    assert 150 <= result.best.american <= 300 and result.best.in_band
    assert all(prop.hits == prop.games and prop.price.exact for prop in result.best.props)


def test_sgp_returns_closest_options_and_an_explanation_instead_of_padding():
    result = build_sgp(_priced_pool(-1000))
    assert result.best is None
    assert 1 <= len(result.closest) <= 3
    assert all(not combo.in_band and combo.american < 150 for combo in result.closest)
    assert "Nothing was padded" in result.message and "+150" in result.message
    expensive = build_sgp(_priced_pool(400))
    assert expensive.best is None and expensive.closest and expensive.closest[0].american > 300


def test_sgp_needs_three_priced_props():
    assert build_sgp(_priced_pool(-200, players=1)).best is None
    assert "needs 3" in build_sgp(_priced_pool(-200, players=1)).message
    assert build_sgp([]).best is None


def test_sgp_prefers_non_trap_props():
    table = {"POINTS": rank_table({"HOM": 100.0, "X": 105.0, "Y": 110.0, "Z": 115.0}, verb="allows", noun="points")}
    stats = {"POINTS": [22] * 6, "ASSISTS": [8] * 6, "REBOUNDS": [9] * 6, "THREES": [3] * 6}
    series = [_series("A", stats)]
    legs = [_leg("A", "POINTS", 19.5, -200), _leg("A", "ASSISTS", 5.5, -200), _leg("A", "REBOUNDS", 7.5, -200), _leg("A", "THREES", 2.5, -200)]
    rows = [r for r in evaluate_game(_game(series, defense=table), legs, scope="season") if r.price and r.price.exact]
    assert any(r.is_trap for r in rows)
    best = build_sgp(rows).best
    assert best is not None and best.traps == 0 and all(p.stat != "POINTS" for p in best.props)


def test_sgp_never_combines_conflicting_legs():
    data = _game([_series("A", {"POINTS": [32, 31, 30, 35, 40, 30]}), _series("B", {"POINTS": [22] * 6}), _series("C", {"POINTS": [21] * 6})])
    legs = [_leg("A", "POINTS", 9.5, -200), _leg("A", "POINTS", 24.5, -200), _leg("B", "POINTS", 19.5, -200), _leg("C", "POINTS", 19.5, -200)]
    pool = [r for r in evaluate_game(data, legs, scope="season") if r.price and r.price.exact]
    assert sum(1 for r in pool if r.player == "A") == 2
    result = build_sgp(pool)
    assert result.best is not None
    assert sum(1 for prop in result.best.props if prop.player == "A") == 1


def test_sgp_ignores_missed_by_one_rows():
    values = [12, 14, 9, 11, 13, 10, 12, 15, 11, 10]
    data = _game([_series(f"P{i}", {"POINTS": values}) for i in range(3)])
    legs = [_leg(f"P{i}", "POINTS", 9.5, -200) for i in range(3)]
    rows = evaluate_game(data, legs, scope="L10", missed=True)
    assert rows and build_sgp(rows).best is None


# --------------------------------------------------------------------------- the card


def _card_script():
    import streamlit as st

    from src.api.data_models import MarketLeg
    from src.engine.hit_rates import GameData, PlayerSeries
    from src.ui.hit_rates_view import render_hit_rates
    from types import SimpleNamespace as NS

    def leg(player, market, line, american=-200):
        decimal = 1 + 100 / -american
        return MarketLeg(
            leg_id=f"g1|{player}|{market}|{line}", game_id="g1", matchup="AWY @ HOM", game_date="2026-10-30",
            start_time_utc="2026-10-30T23:00:00Z", market_type=market, market_key=f"g1|{market}|{player}|{line}",
            target_entity=player, team_abbrev="AWY", selection=player, side="OVER", line=line, american_odds=american,
            decimal_odds=decimal, implied_prob=1 / decimal, fair_prob=0.9 / decimal, model_prob=0.5, ev=0.0,
            bookmaker="draftkings", rationale="", sport="NBA",
        )

    st.session_state.setdefault("slip", [])
    stats = {"POINTS": [22, 21, 20, 25, 30, 20], "ASSISTS": [6, 7, 8, 9, 6, 7], "REBOUNDS": [8, 9, 10, 8, 9, 11]}
    series = [PlayerSeries(name=f"Player {c} $", sport="NBA", team="AWY", opponent="HOM", stats=dict(stats)) for c in "ABC"]
    series.append(PlayerSeries(name="No Price", sport="NBA", team="HOM", opponent="AWY", stats={"POINTS": [30] * 6}))
    st.session_state.setdefault("hr_scans", {"NBA|g1": GameData(sport="NBA", game_id="g1", away="AWY", home="HOM", series=series, loaded=4, total=4)})
    legs = [leg(f"Player {c} $", m, line) for c in "ABC" for m, line in (("POINTS", 19.5), ("ASSISTS", 5.5))]
    board = NS(legs=legs, prop_legs=[])
    game = NS(game_id="g1", away=NS(abbrev="AWY"), home=NS(abbrev="HOM"), game_state="FUT")

    def add(quote):
        if not any(item["id"] == quote["id"] for item in st.session_state.slip):
            st.session_state.slip.append(quote)

    render_hit_rates(board, game, league="NBA", preferred_book="", add_quote=add, ctx="t", autoscan=False)


def _text(app) -> str:
    return " ".join(block.value for block in app.markdown)


def test_card_renders_rows_header_prices_and_escapes_dollars():
    app = AppTest.from_function(_card_script, default_timeout=60)
    app.run()
    assert not app.exception, app.exception
    text = _text(app)
    assert "AWY @ HOM" in text and "100% HIT RATES" in text
    assert "These props have cashed in every game this season (or L10)." in text
    assert "20+ Points" in text and "6/6" in text and "cushion" in text
    assert "no line posted" in text
    assert "&#36;" in text and "$" not in text.replace("&#36;", "")
    assert any(button.label == "Build Undefeated SGP" for button in app.button)
    disabled = [button for button in app.button if button.label == "+ Add to Slip" and button.proto.disabled]
    assert disabled, "a prop with no price must have its add button disabled"


def test_card_add_to_slip_and_build_sgp_use_the_slip_data_shape():
    app = AppTest.from_function(_card_script, default_timeout=60)
    app.run()
    next(b for b in app.button if b.label == "+ Add to Slip" and not b.proto.disabled).click()
    app.run()
    assert not app.exception, app.exception
    assert len(app.session_state["slip"]) == 1
    quote = app.session_state["slip"][0]
    assert quote["sport"] == "NBA" and quote["pick"] == "OVER" and 0 < quote["probability"] < 1
    for key in ("id", "selection", "american", "decimal", "book", "game_key", "matchup", "sample"):
        assert key in quote
    next(b for b in app.button if b.label == "Build Undefeated SGP").click()
    app.run()
    assert not app.exception, app.exception
    assert len(app.session_state["slip"]) >= 3


def test_card_modes_render_without_error():
    app = AppTest.from_function(_card_script, default_timeout=60)
    app.run()
    for mode in ("Last 10 Games (100%)", "Last 5 Games (100%)", "90% Missed by 1"):
        app.session_state["hr-mode-t"] = mode
        app.run()
        assert not app.exception, (mode, app.exception)
    assert "No player" in " ".join(info.value for info in app.info)


def test_card_waits_for_the_user_before_fetching():
    def script():
        import streamlit as st
        from types import SimpleNamespace as NS
        from src.ui.hit_rates_view import render_hit_rates

        game = NS(game_id="g9", away=NS(abbrev="AWY"), home=NS(abbrev="HOM"), game_state="FUT")
        render_hit_rates(NS(legs=[], prop_legs=[]), game, league="NBA", preferred_book="", add_quote=lambda q: None, ctx="w", autoscan=False)

    app = AppTest.from_function(script, default_timeout=30)
    app.run()
    assert not app.exception, app.exception
    assert any(button.label.startswith("Scan 100% hit rates") for button in app.button)
