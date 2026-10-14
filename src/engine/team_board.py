"""NFL and NBA boards: team strength, synthetic prices, and model probabilities."""

from __future__ import annotations

import hashlib
import math

from src.api.data_models import Game, MarketLeg, Offer, SlateContext, TeamProfile, TeamSide
from src.api.odds_client import SYNTHETIC_BOOK
from src.engine.devig import (
    american_to_decimal,
    american_to_implied,
    expected_value,
    implied_to_american,
    multiplicative_devig,
    poisson_sf_over,
)

HOME_EDGE = {"NFL": 2.4, "NBA": 3.0}
MARGIN_SD = {"NFL": 13.5, "NBA": 12.0}
TOTAL_SD = {"NFL": 10.0, "NBA": 14.0}
LEAGUE_PPG = {"NFL": 22.5, "NBA": 114.0}

# name, market, season rate. Anytime TD rate is expected touchdowns, not a yard line.
STARS: dict[str, dict[str, list[tuple[str, str, float]]]] = {
    "NFL": {
        "KC": [("Patrick Mahomes", "PASS_YDS", 275), ("Travis Kelce", "REC_YDS", 68), ("Patrick Mahomes", "ANYTIME_TD", 0.55)],
        "BUF": [("Josh Allen", "PASS_YDS", 255), ("James Cook", "RUSH_YDS", 78), ("Josh Allen", "ANYTIME_TD", 0.7)],
        "PHI": [("Jalen Hurts", "PASS_YDS", 230), ("Saquon Barkley", "RUSH_YDS", 95), ("A.J. Brown", "REC_YDS", 78)],
        "SF": [("Brock Purdy", "PASS_YDS", 250), ("Christian McCaffrey", "RUSH_YDS", 85), ("Christian McCaffrey", "ANYTIME_TD", 0.75)],
        "BAL": [("Lamar Jackson", "PASS_YDS", 220), ("Derrick Henry", "RUSH_YDS", 100), ("Lamar Jackson", "ANYTIME_TD", 0.6)],
        "DET": [("Jared Goff", "PASS_YDS", 260), ("Amon-Ra St. Brown", "REC_YDS", 85), ("Jahmyr Gibbs", "RUSH_YDS", 80)],
        "DAL": [("Dak Prescott", "PASS_YDS", 250), ("CeeDee Lamb", "REC_YDS", 90), ("CeeDee Lamb", "ANYTIME_TD", 0.5)],
        "GB": [("Jordan Love", "PASS_YDS", 255), ("Josh Jacobs", "RUSH_YDS", 75), ("Jayden Reed", "REC_YDS", 60)],
        "DEN": [("Bo Nix", "PASS_YDS", 230), ("Courtland Sutton", "REC_YDS", 70)],
        "SEA": [("Geno Smith", "PASS_YDS", 240), ("DK Metcalf", "REC_YDS", 75)],
        "CIN": [("Joe Burrow", "PASS_YDS", 270), ("Ja'Marr Chase", "REC_YDS", 95), ("Ja'Marr Chase", "ANYTIME_TD", 0.55)],
        "MIN": [("Justin Jefferson", "REC_YDS", 95), ("Aaron Jones", "RUSH_YDS", 70)],
    },
    "NBA": {
        "BOS": [("Jayson Tatum", "POINTS", 28), ("Jayson Tatum", "REBOUNDS", 8.5), ("Derrick White", "THREES", 3.2)],
        "NYK": [("Jalen Brunson", "POINTS", 27), ("Karl-Anthony Towns", "REBOUNDS", 12), ("Jalen Brunson", "ASSISTS", 7.5)],
        "OKC": [("Shai Gilgeous-Alexander", "POINTS", 32), ("Shai Gilgeous-Alexander", "ASSISTS", 6.5)],
        "DEN": [("Nikola Jokic", "POINTS", 28), ("Nikola Jokic", "REBOUNDS", 12.5), ("Nikola Jokic", "ASSISTS", 9.5)],
        "MIA": [("Bam Adebayo", "REBOUNDS", 10), ("Tyler Herro", "POINTS", 23)],
        "BKN": [("Cam Thomas", "POINTS", 24)],
        "LAL": [("Luka Doncic", "POINTS", 30), ("Luka Doncic", "ASSISTS", 8), ("LeBron James", "POINTS", 24)],
        "GSW": [("Stephen Curry", "POINTS", 27), ("Stephen Curry", "THREES", 4.8)],
        "MIL": [("Giannis Antetokounmpo", "POINTS", 31), ("Giannis Antetokounmpo", "REBOUNDS", 11.5)],
        "DAL": [("Kyrie Irving", "POINTS", 25), ("Anthony Davis", "REBOUNDS", 11)],
    },
}

# DEN and DAL appear in both sports. Stars are chosen by the sport when building offers.


def _norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def _clip(value: float, low: float = 0.04, high: float = 0.92) -> float:
    return min(max(value, low), high)


def _profile(abbrev: str, name: str, wins: int, losses: int, scored: float, allowed: float) -> TeamProfile:
    played = max(wins + losses, 1)
    return TeamProfile(
        abbrev=abbrev,
        name=name,
        games_played=played,
        wins=wins,
        losses=losses,
        gf_per_game=scored,
        ga_per_game=allowed,
        l10_gp=min(played, 5),
        l10_wins=max(wins - 1, 0),
        l10_losses=losses,
        l10_gf_per_game=scored * 0.96,
        l10_ga_per_game=allowed * 1.04,
        streak_code="W" if wins >= losses else "L",
        streak_count=1,
    )


def _mock_rows(sport: str) -> tuple[list[tuple], list[tuple]]:
    if sport == "NFL":
        teams = [
            ("KC", "Kansas City Chiefs", 5, 1, 27.0, 19.0),
            ("BUF", "Buffalo Bills", 4, 2, 26.5, 20.0),
            ("PHI", "Philadelphia Eagles", 4, 2, 25.0, 18.5),
            ("SF", "San Francisco 49ers", 3, 3, 23.0, 21.0),
            ("BAL", "Baltimore Ravens", 4, 2, 26.0, 19.5),
            ("DET", "Detroit Lions", 4, 2, 27.5, 22.0),
            ("DAL", "Dallas Cowboys", 3, 3, 22.0, 22.5),
            ("GB", "Green Bay Packers", 3, 3, 24.0, 21.5),
            ("CIN", "Cincinnati Bengals", 3, 3, 25.5, 23.0),
            ("MIN", "Minnesota Vikings", 4, 2, 24.5, 20.5),
            ("DEN", "Denver Broncos", 3, 2, 22.5, 20.0),
            ("SEA", "Seattle Seahawks", 3, 2, 23.5, 21.0),
        ]
        games = [
            ("nfl-mock-1", "BUF", "KC", "T17:00:00Z"),
            ("nfl-mock-2", "SF", "PHI", "T17:00:00Z"),
            ("nfl-mock-3", "DET", "BAL", "T20:25:00Z"),
            ("nfl-mock-4", "GB", "DAL", "T20:25:00Z"),
            ("nfl-mock-5", "MIN", "CIN", "T17:00:00Z"),
            ("nfl-mock-6", "SEA", "DEN", "T00:20:00Z"),
        ]
        return teams, games
    teams = [
        ("BOS", "Boston Celtics", 4, 1, 120.0, 110.0),
        ("NYK", "New York Knicks", 3, 2, 116.0, 112.0),
        ("OKC", "Oklahoma City Thunder", 5, 0, 122.0, 105.0),
        ("DEN", "Denver Nuggets", 3, 2, 118.0, 114.0),
        ("MIA", "Miami Heat", 2, 1, 112.0, 110.0),
        ("BKN", "Brooklyn Nets", 3, 0, 115.0, 108.0),
        ("LAL", "Los Angeles Lakers", 3, 2, 117.0, 115.0),
        ("GSW", "Golden State Warriors", 2, 3, 114.0, 116.0),
        ("MIL", "Milwaukee Bucks", 3, 2, 119.0, 116.0),
        ("DAL", "Dallas Mavericks", 2, 3, 115.0, 117.0),
    ]
    games = [
        ("nba-mock-1", "NYK", "BOS", "T23:30:00Z"),
        ("nba-mock-2", "DEN", "OKC", "T00:00:00Z"),
        ("nba-mock-3", "BKN", "MIA", "T23:00:00Z"),
        ("nba-mock-4", "GSW", "LAL", "T02:00:00Z"),
        ("nba-mock-5", "DAL", "MIL", "T00:30:00Z"),
    ]
    return teams, games


def load_mock_team_context(sport: str, slate_date: str) -> SlateContext:
    team_rows, game_rows = _mock_rows(sport)
    teams = {abbrev: _profile(abbrev, name, wins, losses, scored, allowed) for abbrev, name, wins, losses, scored, allowed in team_rows}
    games = []
    for game_id, away, home, clock in game_rows:
        games.append(
            Game(
                game_id=game_id,
                game_date=slate_date,
                start_time_utc=f"{slate_date}{clock}",
                game_state="FUT",
                venue=f"{sport} mock venue",
                away=TeamSide(team_id=0, abbrev=away, name=teams[away].name),
                home=TeamSide(team_id=0, abbrev=home, name=teams[home].name),
            )
        )
    return SlateContext(
        slate_date=slate_date,
        source="mock",
        sport=sport,
        games=games,
        teams=teams,
        players=[],
        goalies=[],
        notes=[f"Official {sport} feed unavailable. Showing an offline slate with synthetic prices."],
    )


def _expected_points(offense: TeamProfile, defense: TeamProfile, sport: str, *, model: bool) -> float:
    league = LEAGUE_PPG[sport]
    off = offense.l10_gf_per_game if model else offense.gf_per_game
    deff = defense.l10_ga_per_game if model else defense.ga_per_game
    blended = 0.5 * off + 0.5 * deff
    if not model:
        blended = 0.8 * blended + 0.2 * league
    return max(blended, 8.0)


def _score_pair(home: TeamProfile, away: TeamProfile, sport: str, *, model: bool) -> tuple[float, float]:
    home_points = _expected_points(home, away, sport, model=model)
    away_points = _expected_points(away, home, sport, model=model)
    edge = HOME_EDGE[sport] if model else HOME_EDGE[sport] * 0.65
    return home_points + edge / 2, away_points - edge / 2


def _cover_prob(mean_margin: float, line: float, sport: str) -> float:
    # line is from the team's perspective (home -3.5). Cover when margin + line > 0.
    threshold = -line
    z_score = (threshold - mean_margin) / MARGIN_SD[sport]
    return _clip(1.0 - _norm_cdf(z_score))


def _total_prob(mean_total: float, line: float, sport: str, side: str) -> float:
    z_score = (line - mean_total) / TOTAL_SD[sport]
    over = _clip(1.0 - _norm_cdf(z_score))
    return over if side == "OVER" else _clip(1.0 - over)


def _offer(game: Game, **kwargs) -> Offer:
    return Offer(
        game_id=game.game_id,
        matchup=f"{game.away.abbrev} @ {game.home.abbrev}",
        game_date=game.game_date,
        start_time_utc=game.start_time_utc,
        bookmaker=SYNTHETIC_BOOK,
        **kwargs,
    )


def _half_line(value: float) -> float:
    rounded = round(value * 2) / 2
    if float(rounded).is_integer():
        rounded += 0.5 if value >= rounded else -0.5
    return rounded


def synthetic_team_offers(context: SlateContext) -> list[Offer]:
    sport = context.sport
    offers: list[Offer] = []
    for game in context.games:
        home = context.teams.get(game.home.abbrev)
        away = context.teams.get(game.away.abbrev)
        if home is None or away is None:
            continue
        offers.extend(_game_offers(game, home, away, sport))
    return offers


def _game_offers(game: Game, home: TeamProfile, away: TeamProfile, sport: str) -> list[Offer]:
    home_points, away_points = _score_pair(home, away, sport, model=False)
    margin = home_points - away_points
    home_spread = _half_line(-margin)
    total_line = _half_line(home_points + away_points)
    home_cover = _cover_prob(margin, home_spread, sport)
    away_cover = _clip(1.0 - home_cover)
    home_ml = _clip(_cover_prob(margin, -0.5, sport))
    away_ml = _clip(1.0 - home_ml)
    home_ml_price, away_ml_price = _juiced(home_ml, away_ml)
    home_spread_price, away_spread_price = _juiced(home_cover, away_cover)
    over = _total_prob(home_points + away_points, total_line, sport, "OVER")
    over_price, under_price = _juiced(over, _clip(1.0 - over))
    offers = [
        _offer(game, market_type="MONEYLINE", market_key=f"{game.game_id}|MONEYLINE", target_entity=home.name, selection=f"{home.name} ML", side="HOME", american_odds=home_ml_price, team_abbrev=home.abbrev, opponent_abbrev=away.abbrev),
        _offer(game, market_type="MONEYLINE", market_key=f"{game.game_id}|MONEYLINE", target_entity=away.name, selection=f"{away.name} ML", side="AWAY", american_odds=away_ml_price, team_abbrev=away.abbrev, opponent_abbrev=home.abbrev),
        _offer(game, market_type="SPREAD", market_key=f"{game.game_id}|SPREAD|{abs(home_spread):.1f}", target_entity=home.name, selection=f"{home.name} {home_spread:+.1f}", side="HOME", line=home_spread, american_odds=home_spread_price, team_abbrev=home.abbrev, opponent_abbrev=away.abbrev),
        _offer(game, market_type="SPREAD", market_key=f"{game.game_id}|SPREAD|{abs(home_spread):.1f}", target_entity=away.name, selection=f"{away.name} {-home_spread:+.1f}", side="AWAY", line=-home_spread, american_odds=away_spread_price, team_abbrev=away.abbrev, opponent_abbrev=home.abbrev),
        _offer(game, market_type="TOTAL", market_key=f"{game.game_id}|TOTAL|{total_line:.1f}", target_entity=f"{game.away.abbrev} @ {game.home.abbrev}", selection=f"Over {total_line:.1f}", side="OVER", line=total_line, american_odds=over_price),
        _offer(game, market_type="TOTAL", market_key=f"{game.game_id}|TOTAL|{total_line:.1f}", target_entity=f"{game.away.abbrev} @ {game.home.abbrev}", selection=f"Under {total_line:.1f}", side="UNDER", line=total_line, american_odds=under_price),
    ]
    for team, opponent in ((home, away), (away, home)):
        for name, market, rate in STARS.get(sport, {}).get(team.abbrev, []):
            offers.extend(_prop_offers(game, team, opponent, name, market, rate, sport))
    return offers


def _juiced(left: float, right: float) -> tuple[int, int]:
    scale = 1.045
    return implied_to_american(min(left * scale, 0.96)), implied_to_american(min(right * scale, 0.96))


def _prop_offers(game: Game, team: TeamProfile, opponent: TeamProfile, name: str, market: str, rate: float, sport: str) -> list[Offer]:
    pace = team.gf_per_game / LEAGUE_PPG[sport]
    if market == "ANYTIME_TD":
        chance = _clip(1.0 - math.exp(-rate * (0.9 + 0.1 * pace)))
        yes_price, no_price = _juiced(chance, _clip(1.0 - chance))
        key = f"{game.game_id}|ANYTIME_TD|{name}"
        return [
            _offer(game, market_type="ANYTIME_TD", market_key=key, target_entity=name, selection=f"{name} anytime TD", side="YES", line=0.5, american_odds=yes_price, team_abbrev=team.abbrev, opponent_abbrev=opponent.abbrev),
            _offer(game, market_type="ANYTIME_TD", market_key=key, target_entity=name, selection=f"{name} no TD", side="NO", line=0.5, american_odds=no_price, team_abbrev=team.abbrev, opponent_abbrev=opponent.abbrev, selectable=False),
        ]
    line = _half_line(rate)
    adjusted = rate * (0.92 + 0.08 * (opponent.ga_per_game / LEAGUE_PPG[sport]))
    over, _push = poisson_sf_over(max(adjusted, 0.2) if line < 20 else max(adjusted / 15, 0.4), 0.5 if line >= 20 else line)
    # Yardage markets are not Poisson counts of 250. Use a normal around the rate instead.
    if line >= 20:
        over = _total_prob(adjusted, line, "NBA" if sport == "NBA" else "NFL", "OVER")
    over_price, under_price = _juiced(over, _clip(1.0 - over))
    key = f"{game.game_id}|{market}|{name}|{line:.1f}"
    return [
        _offer(game, market_type=market, market_key=key, target_entity=name, selection=f"{name} Over {line:.1f}", side="OVER", line=line, american_odds=over_price, team_abbrev=team.abbrev, opponent_abbrev=opponent.abbrev),
        _offer(game, market_type=market, market_key=key, target_entity=name, selection=f"{name} Under {line:.1f}", side="UNDER", line=line, american_odds=under_price, team_abbrev=team.abbrev, opponent_abbrev=opponent.abbrev, selectable=False),
    ]


def price_team_offers(context: SlateContext, offers: list[Offer]) -> list[MarketLeg]:
    grouped: dict[str, list[Offer]] = {}
    for offer in offers:
        grouped.setdefault(offer.market_key, []).append(offer)
    fair_by_id: dict[int, float] = {}
    for group in grouped.values():
        implied = [american_to_implied(offer.american_odds) for offer in group]
        fair = multiplicative_devig(implied) if len(group) >= 2 and sum(implied) > 0 else [min(implied[0] / 1.045, 0.97)]
        for offer, probability in zip(group, fair):
            fair_by_id[id(offer)] = probability
    legs: list[MarketLeg] = []
    games = {game.game_id: game for game in context.games}
    for offer in offers:
        game = games.get(offer.game_id)
        if game is None:
            continue
        home = context.teams.get(game.home.abbrev)
        away = context.teams.get(game.away.abbrev)
        if home is None or away is None:
            continue
        win = _model_prob(context.sport, game, home, away, offer)
        decimal_odds = american_to_decimal(offer.american_odds)
        implied = american_to_implied(offer.american_odds)
        fair = fair_by_id.get(id(offer), implied)
        digest = hashlib.sha1(f"{offer.market_key}|{offer.side}|{offer.american_odds}|{offer.bookmaker}".encode()).hexdigest()[:16]
        home_points, away_points = _score_pair(home, away, context.sport, model=True)
        rationale = (
            f"Projected score {game.away.abbrev} {away_points:.1f}, {game.home.abbrev} {home_points:.1f}. "
            f"Model {offer.selection} at {win:.1%}."
        )
        legs.append(
            MarketLeg(
                leg_id=digest,
                game_id=offer.game_id,
                matchup=offer.matchup,
                game_date=offer.game_date,
                start_time_utc=offer.start_time_utc,
                market_type=offer.market_type,
                market_key=offer.market_key,
                target_entity=offer.target_entity,
                team_abbrev=offer.team_abbrev,
                opponent_abbrev=offer.opponent_abbrev,
                selection=offer.selection,
                side=offer.side,
                line=offer.line,
                american_odds=offer.american_odds,
                decimal_odds=decimal_odds,
                implied_prob=implied,
                fair_prob=fair,
                model_prob=win,
                ev=expected_value(win, decimal_odds),
                bookmaker=offer.bookmaker,
                rationale=rationale,
                selectable=offer.selectable,
            )
        )
    return legs


def _model_prob(sport: str, game: Game, home: TeamProfile, away: TeamProfile, offer: Offer) -> float:
    home_points, away_points = _score_pair(home, away, sport, model=True)
    margin = home_points - away_points
    if offer.market_type == "MONEYLINE":
        cover = _cover_prob(margin, -0.5, sport)
        return cover if offer.side == "HOME" else _clip(1.0 - cover)
    if offer.market_type == "SPREAD" and offer.line is not None:
        team_margin = margin if offer.side == "HOME" else -margin
        return _cover_prob(team_margin, offer.line, sport)
    if offer.market_type == "TOTAL" and offer.line is not None:
        return _total_prob(home_points + away_points, offer.line, sport, offer.side)
    if offer.market_type == "ANYTIME_TD":
        implied = american_to_implied(offer.american_odds)
        team = home if offer.team_abbrev == home.abbrev else away
        pace = team.gf_per_game / LEAGUE_PPG[sport]
        bumped = _clip(implied * (0.9 + 0.15 * pace))
        return bumped if offer.side == "YES" else _clip(1.0 - bumped)
    if offer.line is not None and offer.market_type in {"PASS_YDS", "RUSH_YDS", "REC_YDS", "POINTS", "REBOUNDS", "ASSISTS", "THREES"}:
        team = home if offer.team_abbrev == home.abbrev else away
        opponent = away if team is home else home
        pace = (team.gf_per_game / LEAGUE_PPG[sport]) * (opponent.ga_per_game / LEAGUE_PPG[sport])
        mean = offer.line * (0.9 + 0.1 * pace)
        over = _total_prob(mean, offer.line, sport, "OVER") if offer.line >= 20 else poisson_sf_over(max(mean, 0.2), offer.line)[0]
        return over if offer.side == "OVER" else _clip(1.0 - over)
    return _clip(american_to_implied(offer.american_odds))
