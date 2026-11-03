"""Nudge a player's season hit rate for tonight's opponent."""

from __future__ import annotations

from src.stats.rates import rate_row

OFFENSE_MARKETS = {"nhl_saves", "nhl_ga"}


def other_side(matchup: str, team: str) -> str | None:
    """Return the opponent abbreviation from a 'AWY @ HOM' matchup."""
    if " @ " not in matchup or not team:
        return None
    away, home = [part.strip() for part in matchup.split(" @ ", 1)]
    token = team.strip().upper()
    if token == home.upper():
        return away
    if token == away.upper():
        return home
    return None


def defense_factor(opponent_rate: float, league_rate: float) -> float:
    """How much easier tonight is than an average opponent. 1.0 is average. Clipped."""
    if league_rate <= 0 or opponent_rate <= 0:
        return 1.0
    return min(max(opponent_rate / league_rate, 0.85), 1.15)


def tonight(sport: str, team: str, matchup: str, market_key: str) -> dict | None:
    """Opponent scoring or defense versus the league, for the player's next game."""
    opponent = other_side(matchup, team)
    if opponent is None:
        return None
    try:
        from src.api.espn_client import fetch_standings

        table = fetch_standings(sport)
    except Exception:
        return None
    profile = next((item for abbrev, item in table.items() if abbrev.upper() == opponent.upper()), None)
    if profile is None:
        return None
    offense = market_key in OFFENSE_MARKETS
    rates = [(item.gf_per_game if offense else item.ga_per_game) for item in table.values()]
    rates = [rate for rate in rates if rate]
    if not rates:
        return None
    average = sum(rates) / len(rates)
    number = profile.gf_per_game if offense else profile.ga_per_game
    verb = "scores" if offense else "allows"
    return {
        "abbrev": opponent,
        "factor": defense_factor(number, average),
        "note": f"vs {opponent} · {verb} {number:.1f}/g, league {average:.1f}",
    }


def adjusted_probability(market, side: str, line: float | None, values: list[float], factor: float) -> float | None:
    """Scale each logged game, then recount how often the line would have hit."""
    if not values or factor <= 0:
        return None
    scaled = [value * factor for value in values]
    row = rate_row(market, side, line, scaled, "")
    return row.probability
