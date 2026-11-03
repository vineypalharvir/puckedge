"""Find the finished game behind a saved leg.

Tickets can mix leagues and some legs are saved without a game feed id. Instead of leaving those
pending forever, look the game up by league, matchup and date, and check that the box score really
is that matchup before grading anything against it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

SPORTS = ("NHL", "NBA", "NFL")

_ONLY_IN = {
    "SOG": ("NHL",),
    "GOALSCORER": ("NHL",),
    "SAVES": ("NHL",),
    "PUCKLINE": ("NHL",),
    "REBOUNDS": ("NBA",),
    "ASSISTS": ("NBA",),
    "THREES": ("NBA",),
    "PASS_YDS": ("NFL",),
    "RUSH_YDS": ("NFL",),
    "REC_YDS": ("NFL",),
    "ANYTIME_TD": ("NFL",),
}


def parse_matchup(text: str | None) -> tuple[str, str] | None:
    """'CLE @ PHI' -> ('CLE', 'PHI') as (away, home)."""
    if not text or "@" not in text:
        return None
    away, _, home = text.partition("@")
    away, home = away.strip().upper(), home.strip().upper()
    return (away, home) if away and home else None


def candidate_sports(market: str, leg_sport: str | None, ticket_sport: str | None) -> list[str]:
    """Leagues to try, most likely first. A market only one league offers is never tried elsewhere."""
    allowed = _ONLY_IN.get(market, SPORTS)
    ordered: list[str] = []
    for sport in (leg_sport, ticket_sport, *SPORTS):
        if sport in allowed and sport not in ordered:
            ordered.append(sport)
    return ordered


def candidate_days(game_date: str | None, created_at: datetime | None, today: date | None = None) -> list[str]:
    """Calendar days a game could sit on. Late tip-offs land on the next UTC day, so look one past."""
    start: date | None = None
    if game_date:
        try:
            start = date.fromisoformat(game_date[:10])
        except ValueError:
            start = None
    if start is None and created_at is not None:
        start = created_at.date()
    if start is None:
        return []
    today = today or date.today()
    span = 2 if game_date else 4
    days = [start + timedelta(days=offset) for offset in range(span)]
    return [day.isoformat() for day in days if day <= today + timedelta(days=1)]


def box_matches(box, matchup: tuple[str, str] | None) -> bool:
    if box is None:
        return False
    if matchup is None:
        return True
    return (box.away_abbrev.upper(), box.home_abbrev.upper()) == matchup


class LegResolver:
    """Caches lookups for one settlement pass so a busy ledger doesn't hammer the feeds."""

    def __init__(self, fetch_boxscore, find_game=None) -> None:
        self._fetch = fetch_boxscore
        self._find = find_game
        self._boxes: dict[tuple[str, str], object] = {}
        self._found: dict[tuple, str | None] = {}

    def _box(self, sport: str, game_id: str):
        key = (sport, game_id)
        if key not in self._boxes:
            try:
                try:
                    self._boxes[key] = self._fetch(game_id, sport)
                except TypeError:
                    self._boxes[key] = self._fetch(game_id)
            except Exception:
                self._boxes[key] = None
        return self._boxes[key]

    def _lookup(self, sport: str, matchup: tuple[str, str], days: list[str]) -> str | None:
        key = (sport, matchup, tuple(days))
        if key not in self._found:
            found = None
            if self._find is not None:
                try:
                    found = self._find(sport, matchup[0], matchup[1], days)
                except Exception:
                    found = None
            self._found[key] = found
        return self._found[key]

    def resolve(self, leg, ticket_sport: str | None, created_at: datetime | None):
        """Return (box, sport, game_id, note). `box` is None when nothing trustworthy was found."""
        matchup = parse_matchup(leg.matchup)
        sports = candidate_sports(leg.market_type, getattr(leg, "sport", None), ticket_sport)
        if leg.game_id:
            for sport in sports:
                box = self._box(sport, leg.game_id)
                if box_matches(box, matchup):
                    return box, sport, leg.game_id, ""
        if matchup is not None:
            days = candidate_days(leg.game_date, created_at)
            for sport in sports:
                game_id = self._lookup(sport, matchup, days) if days else None
                if not game_id:
                    continue
                box = self._box(sport, game_id)
                if box_matches(box, matchup):
                    return box, sport, game_id, ""
            where = f" on {leg.game_date}" if leg.game_date else ""
            return None, None, "", f"Could not find {leg.matchup}{where} in the {'/'.join(sports)} schedule yet."
        return None, None, "", "This leg has no matchup or game id saved, so there is nothing to look up."
