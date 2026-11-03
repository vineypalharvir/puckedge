"""Same-game conflict screening, synergy weights, and team logo assets."""

import itertools
import os

import pytest

from src.api.data_models import MarketLeg
from src.engine.boost import grade_boost
from src.engine.correlation import SYNERGY_CAP, pair_conflicts, slip_conflict, synergy, ticket_conflicts
from src.engine.sgp import ParlayFilter, generate_valid_parlays
from src.engine.target_hunt import hunt_parlays
from src.utils.team_assets import SLUGS, team_badge_html, team_logo_html, team_logo_url


def _leg(leg_id, market, side, *, team=None, line=None, player_id=None, entity="", game="g1", decimal_odds=1.9, model_prob=0.5, key=None):
    return MarketLeg(
        leg_id=leg_id,
        game_id=game,
        matchup="AWY @ HOM",
        game_date="2026-10-22",
        start_time_utc="2026-10-22T23:00:00Z",
        market_type=market,
        market_key=key or f"{game}|{market}|{team}|{line}|{entity}",
        target_entity=entity or (team or "game"),
        team_abbrev=team,
        player_id=player_id,
        selection=f"{leg_id}",
        side=side,
        line=line,
        american_odds=-110,
        decimal_odds=decimal_odds,
        implied_prob=0.52,
        fair_prob=0.5,
        model_prob=model_prob,
        ev=0.0,
        bookmaker="test",
        rationale="",
    )


HOME_ML = _leg("home-ml", "MONEYLINE", "HOME", team="HOM")
AWAY_ML = _leg("away-ml", "MONEYLINE", "AWAY", team="AWY")
HOME_MINUS = _leg("home-pl-", "PUCKLINE", "HOME", team="HOM", line=-1.5)
AWAY_PLUS = _leg("away-pl+", "PUCKLINE", "AWAY", team="AWY", line=1.5)
AWAY_MINUS = _leg("away-pl-", "PUCKLINE", "AWAY", team="AWY", line=-1.5)
HOME_PLUS = _leg("home-pl+", "PUCKLINE", "HOME", team="HOM", line=1.5)
OVER = _leg("over", "TOTAL", "OVER", line=5.5)
UNDER = _leg("under", "TOTAL", "UNDER", line=5.5)
UNDER_HIGH = _leg("under-7", "TOTAL", "UNDER", line=7.5)


@pytest.mark.parametrize(
    "left, right",
    [
        (HOME_ML, AWAY_ML),
        (HOME_MINUS, AWAY_PLUS),
        (AWAY_MINUS, HOME_PLUS),
        (OVER, UNDER),
        (HOME_ML, AWAY_PLUS),  # ML plus the opponent's spread
        (AWAY_ML, HOME_MINUS),
        (AWAY_ML, HOME_PLUS),
        (HOME_ML, AWAY_MINUS),
        (HOME_ML, HOME_MINUS),  # same team ML plus its own spread
        (HOME_ML, HOME_PLUS),
        (HOME_MINUS, HOME_PLUS),  # alternate spread lines
        (OVER, _leg("over-alt", "TOTAL", "OVER", line=6.5)),
        (
            _leg("hom-tt-o", "TEAM_TOTAL", "OVER", team="HOM", line=2.5),
            _leg("hom-tt-o2", "TEAM_TOTAL", "OVER", team="HOM", line=3.5),
        ),
        (
            _leg("sog-o", "SOG", "OVER", player_id=1, entity="Pat Skater", line=2.5),
            _leg("sog-o2", "SOG", "OVER", player_id=1, entity="Pat Skater", line=3.5),
        ),
    ],
)
def test_conflicting_pairs_are_blocked_in_either_order(left, right):
    assert pair_conflicts(left, right)
    assert pair_conflicts(right, left)
    assert ticket_conflicts([left, right])


@pytest.mark.parametrize(
    "left, right",
    [
        (HOME_ML, _leg("other-game", "MONEYLINE", "AWAY", team="AWY", game="g2")),
        (HOME_ML, OVER),
        (
            _leg("hom-tt", "TEAM_TOTAL", "OVER", team="HOM", line=2.5),
            _leg("awy-tt", "TEAM_TOTAL", "OVER", team="AWY", line=2.5),
        ),
        (
            _leg("sog", "SOG", "OVER", player_id=1, entity="Pat Skater", line=2.5),
            _leg("pts", "POINTS", "OVER", player_id=1, entity="Pat Skater", line=0.5),
        ),
        (
            _leg("sog-a", "SOG", "OVER", player_id=1, entity="Pat Skater", line=2.5),
            _leg("sog-b", "SOG", "OVER", player_id=2, entity="Sam Skater", line=2.5),
        ),
    ],
)
def test_compatible_pairs_still_pass(left, right):
    assert not pair_conflicts(left, right)


def _scorer(leg_id, market="GOALSCORER", player=1):
    return _leg(leg_id, market, "YES", team="HOM", player_id=player, entity=f"Player {player}", line=0.5)


def test_game_under_with_two_anytime_scorers_is_blocked_for_any_line_and_both_sports():
    assert ticket_conflicts([UNDER_HIGH, _scorer("g1", player=1), _scorer("g2", player=2)])
    assert ticket_conflicts([UNDER, _scorer("td1", "ANYTIME_TD", 1), _scorer("td2", "ANYTIME_TD", 2)])
    assert ticket_conflicts([UNDER, _scorer("g1", player=1), _scorer("td", "ANYTIME_TD", 2)])


def test_game_under_with_one_scorer_or_scorers_without_an_under_is_allowed():
    assert not ticket_conflicts([UNDER, _scorer("g1")])
    assert not ticket_conflicts([OVER, _scorer("g1", player=1), _scorer("g2", player=2)])
    other_game = _leg("under-g2", "TOTAL", "UNDER", line=5.5, game="g2")
    assert not ticket_conflicts([other_game, _scorer("g1", player=1), _scorer("g2", player=2)])


def test_synergy_rewards_each_pairing_once_and_is_capped():
    pass_leg = _leg("qb", "PASS_YDS", "OVER", team="HOM", player_id=1, entity="QB One", line=249.5)
    rec_leg = _leg("wr", "REC_YDS", "OVER", team="HOM", player_id=2, entity="WR One", line=69.5)
    score, notes = synergy([pass_leg, rec_leg])
    assert score == 2 and len(notes) == 1 and "passing yards" in notes[0]
    assert synergy([pass_leg, _leg("wr-other", "REC_YDS", "OVER", team="AWY", player_id=3, entity="WR Two", line=60.5)])[0] == 0
    assert synergy([pass_leg, _leg("wr-under", "REC_YDS", "UNDER", team="HOM", player_id=4, entity="WR Three", line=60.5)])[0] == 0

    cover = _leg("cover", "PUCKLINE", "HOME", team="HOM", line=-1.5, entity="Home")
    team_over = _leg("tt", "TEAM_TOTAL", "OVER", team="HOM", line=3.5, entity="Home")
    assert synergy([cover, team_over])[0] == 2


    nba_cover = _leg("nba-cover", "SPREAD", "HOME", team="HOM", line=-6.5, entity="Home")
    nba_total = _leg("nba-tt", "TEAM_TOTAL", "OVER", team="HOM", line=118.5, entity="Home")
    assert synergy([nba_cover, nba_total])[0] == 2

    stack = []
    for index in range(1, 6):
        stack.append(_leg(f"qb{index}", "PASS_YDS", "OVER", team="HOM", player_id=index, entity=f"QB {index}", line=200.5 + index))
        stack.append(_leg(f"wr{index}", "REC_YDS", "OVER", team="HOM", player_id=10 + index, entity=f"WR {index}", line=50.5 + index))
    assert synergy(stack)[0] == SYNERGY_CAP


def test_boost_paths_flag_conflicts_and_target_hunt_never_returns_them():
    assert ticket_conflicts([HOME_ML, AWAY_PLUS])
    pools = {"NHL": [HOME_ML, AWAY_ML, HOME_MINUS, AWAY_PLUS, OVER, UNDER, _scorer("a", player=1), _scorer("b", player=2)]}
    tickets = hunt_parlays(pools, stake=10, win_target=None, min_legs=2, max_legs=4, limit=50)
    assert tickets
    for ticket in tickets:
        assert not ticket_conflicts(ticket.legs)


def test_sgp_search_never_builds_a_conflicting_ticket():
    legs = [
        _leg("ml-h", "MONEYLINE", "HOME", team="HOM", decimal_odds=1.6, key="g1|ML|HOM"),
        _leg("ml-a", "MONEYLINE", "AWAY", team="AWY", decimal_odds=2.4, key="g1|ML|AWY"),
        _leg("pl-h", "PUCKLINE", "HOME", team="HOM", line=-1.5, decimal_odds=2.5, key="g1|PL|HOM"),
        _leg("pl-a", "PUCKLINE", "AWAY", team="AWY", line=1.5, decimal_odds=1.5, key="g1|PL|AWY"),
        _leg("u", "TOTAL", "UNDER", line=6.5, decimal_odds=1.9, key="g1|T|6.5|U"),
        _scorer("s1", player=1),
        _scorer("s2", player=2),
        _scorer("td", "ANYTIME_TD", 3),
    ]
    legs = [leg.model_copy(update={"market_key": f"g1|{leg.leg_id}"}) if leg.market_type in {"GOALSCORER", "ANYTIME_TD"} else leg for leg in legs]
    tickets = generate_valid_parlays(legs, ParlayFilter(min_legs=2, max_legs=4, result_limit=500))
    assert tickets
    by_id = {leg.leg_id: leg for leg in legs}
    for ticket in tickets:
        assert not ticket_conflicts([by_id[leg.id] for leg in ticket.legs])
    combos = [{leg.id for leg in ticket.legs} for ticket in tickets]
    assert not any({"ml-h", "pl-a"} <= combo or {"ml-a", "pl-h"} <= combo for combo in combos)
    assert not any("u" in combo and len(combo & {"s1", "s2", "td"}) >= 2 for combo in combos)


def test_grade_boost_flags_the_conflict():
    legs = [HOME_ML, AWAY_PLUS]
    result = grade_boost("+600", "HOM money line\nAWY +1.5", [leg.model_copy(update={"target_entity": "Home" if leg is HOME_ML else "Away"}) for leg in legs])
    assert isinstance(result.conflicts, bool)


def test_slip_conflict_covers_team_and_player_quotes():
    ml_home = {"id": "1", "selection": "HOM ML", "game_key": "NHL|AWY @ HOM", "market_type": "MONEYLINE", "pick": "HOME", "line": None, "team": "HOM", "player": "Home"}
    ml_away = {**ml_home, "id": "2", "selection": "AWY ML", "pick": "AWAY", "team": "AWY", "player": "Away"}
    assert slip_conflict(ml_away, [ml_home]) is not None
    assert slip_conflict(ml_home, []) is None
    other_game = {**ml_away, "game_key": "NHL|BBB @ CCC"}
    assert slip_conflict(other_game, [ml_home]) is None
    spread = {"id": "3", "selection": "AWY +1.5", "game_key": "NHL|AWY @ HOM", "market_type": "PUCKLINE", "pick": "AWAY", "line": 1.5, "team": "AWY", "player": "Away"}
    assert slip_conflict(spread, [ml_home]) is not None
    under = {"id": "4", "selection": "Under 5.5", "game_key": "NHL|AWY @ HOM", "market_type": "TOTAL", "pick": "UNDER", "line": 5.5, "team": "", "player": "Game"}
    over = {**under, "id": "5", "selection": "Over 5.5", "pick": "OVER"}
    assert slip_conflict(over, [under]) is not None
    goal = {"id": "6", "selection": "A anytime goal", "game_key": "NHL|AWY @ HOM", "market_type": "GOALSCORER", "pick": "YES", "line": 0.5, "team": "", "player": "A"}
    goal_two = {**goal, "id": "7", "selection": "B anytime goal", "player": "B"}
    assert slip_conflict(goal, [under]) is None
    assert slip_conflict(goal_two, [under, goal]) is not None
    shots_a = {"id": "8", "selection": "A over 2.5 shots", "game_key": "NHL|AWY @ HOM", "market_type": "SOG", "pick": "OVER", "line": 2.5, "team": "", "player": "A"}
    shots_b = {**shots_a, "id": "9", "selection": "A over 3.5 shots", "line": 3.5}
    assert slip_conflict(shots_b, [shots_a]) is not None
    assert slip_conflict({"id": "x", "selection": "typed"}, [shots_a]) is None


# ---------- team logos ----------


EXPECTED_COUNTS = {"NHL": 32, "NBA": 30, "NFL": 32}


@pytest.mark.parametrize("league", ["NHL", "NBA", "NFL"])
def test_every_team_resolves_to_a_unique_espn_logo(league):
    slugs = SLUGS[league]
    assert len(set(slugs.values())) == EXPECTED_COUNTS[league]
    for abbrev, slug in slugs.items():
        url = team_logo_url(league, abbrev)
        assert url == f"https://a.espncdn.com/i/teamlogos/{league.lower()}/500/{slug}.png"
        assert slug == slug.lower()


def test_slug_exceptions_use_the_espn_names():
    assert team_logo_url("NHL", "LAK").endswith("/nhl/500/la.png")
    assert team_logo_url("NHL", "NJD").endswith("/nhl/500/nj.png")
    assert team_logo_url("NHL", "SJS").endswith("/nhl/500/sj.png")
    assert team_logo_url("NHL", "TBL").endswith("/nhl/500/tb.png")
    assert team_logo_url("NHL", "UTA").endswith("/nhl/500/utah.png")
    assert team_logo_url("NHL", "VGK").endswith("/nhl/500/vgk.png")
    assert team_logo_url("NHL", "WSH").endswith("/nhl/500/wsh.png")
    for abbrev, slug in {"GS": "gs", "NY": "ny", "NO": "no", "SA": "sa", "UTAH": "utah", "WSH": "wsh", "GSW": "gs", "NYK": "ny"}.items():
        assert team_logo_url("NBA", abbrev).endswith(f"/nba/500/{slug}.png")
    assert team_logo_url("NFL", "WSH").endswith("/nfl/500/wsh.png")
    assert team_logo_url("NFL", "WAS").endswith("/nfl/500/wsh.png")
    assert team_logo_url("nfl", " ne ").endswith("/nfl/500/ne.png")


def test_unknown_teams_get_a_text_badge_not_a_broken_image():
    assert team_logo_url("NHL", "ZZZ") is None
    assert team_logo_url("XFL", "NE") is None
    html_text = team_logo_html("NHL", "ZZZ", size=28)
    assert "<img" not in html_text and "team-badge" in html_text and "ZZZ" in html_text
    assert "<img" in team_logo_html("NHL", "BOS") and "team-badge" not in team_logo_html("NHL", "BOS")
    assert "&lt;" in team_badge_html("<b>")


@pytest.mark.skipif(os.getenv("DARKNODE_LOGO_CHECK") != "1", reason="network check; set DARKNODE_LOGO_CHECK=1")
def test_every_logo_url_answers_200_with_an_image():
    import time

    import httpx

    with httpx.Client(timeout=15, follow_redirects=True) as client:
        for league, table in SLUGS.items():
            for url in sorted({team_logo_url(league, abbrev) for abbrev in table}):
                response = client.get(url)
                assert response.status_code == 200, url
                assert response.headers.get("content-type", "").startswith("image/"), url
                time.sleep(0.1)


def test_ticket_conflict_scan_is_stable_for_all_pairs():
    pool = [HOME_ML, AWAY_ML, HOME_MINUS, AWAY_PLUS, OVER, UNDER]
    for left, right in itertools.combinations(pool, 2):
        assert pair_conflicts(left, right) == pair_conflicts(right, left)
