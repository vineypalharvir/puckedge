"""Player hit chances from public NFL, NBA, and NHL game logs."""

from __future__ import annotations

import hashlib
import logging
import math
import re

from src.api.data_models import MarketLeg
from src.api.http_client import get_json
from src.engine.boost import is_player_line
from src.engine.devig import expected_value, implied_to_american

logger = logging.getLogger(__name__)

SEARCH_URL = "https://site.web.api.espn.com/apis/common/v3/search"
OVERVIEW_URL = "https://site.web.api.espn.com/apis/common/v3/sports/{sport}/{league}/athletes/{athlete_id}/overview"

STAT_WORDS = {
    "rushingTouchdowns": "rush TD",
    "receivingTouchdowns": "rec TD",
    "passingTouchdowns": "pass TD",
    "goals": "goals",
    "points": "points",
    "rebounds": "rebounds",
    "assists": "assists",
    "threePointFieldGoalsMade": "threes",
    "rushingYards": "rush yards",
    "receivingYards": "rec yards",
    "passingYards": "pass yards",
    "shotsTotal": "shots",
}


def _number(value) -> float:
    cleaned = str(value).replace(",", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _last_name(name: str) -> str:
    parts = [part for part in re.sub(r"[^a-z]", " ", name.lower()).split() if len(part) > 1]
    return parts[-1] if parts else ""


def _league_hint(line: str) -> str:
    flat = line.lower()
    if any(word in flat for word in ("td", "touchdown", "rush", "rec ", "receiving", "pass")):
        return "nfl"
    if any(word in flat for word in ("rebound", "assist", "three", "points")):
        return "nba"
    if "goal" in flat:
        return "nhl"
    return ""


def prop_spec(line: str, league: str) -> tuple[str, set[str], float, bool, str]:
    """Market, stat names, line, over/under, and a short label."""
    flat = re.sub(r"[^a-z0-9.+]+", " ", line.lower()).strip()
    over = "under" not in flat
    found = re.search(r"(?:over|under)\s+(\d+(?:\.\d+)?)", flat)
    number = float(found.group(1)) if found else None
    if any(word in flat for word in ("td", "touchdown")) or (league == "nfl" and "score" in flat):
        return "ANYTIME_TD", {"rushingTouchdowns", "receivingTouchdowns", "passingTouchdowns"}, 0.5, True, "anytime TD"
    if "rebound" in flat:
        return "REBOUNDS", {"rebounds"}, number if number is not None else 0.5, over, "rebounds"
    if "assist" in flat:
        return "ASSISTS", {"assists"}, number if number is not None else 0.5, over, "assists"
    if "three" in flat:
        return "THREES", {"threePointFieldGoalsMade"}, number if number is not None else 0.5, over, "threes"
    if "rush" in flat:
        return "RUSH_YDS", {"rushingYards"}, number if number is not None else 0.5, over, "rush yards"
    if "receiv" in flat or re.search(r"\brec\b", flat):
        return "REC_YDS", {"receivingYards"}, number if number is not None else 0.5, over, "rec yards"
    if "pass" in flat:
        return "PASS_YDS", {"passingYards"}, number if number is not None else 0.5, over, "pass yards"
    if "point" in flat or league == "nba":
        return "POINTS", {"points"}, number if number is not None else 0.5, over, "points"
    if league == "nhl" or "goal" in flat or "score" in flat:
        return "GOALSCORER", {"goals"}, 0.5, True, "anytime goal"
    return "ANYTIME_TD", {"rushingTouchdowns", "receivingTouchdowns", "passingTouchdowns"}, 0.5, True, "anytime TD"


def player_query(line: str) -> str:
    text = re.sub(
        r"(?i)\b(money\s*line|moneyline|anytime\s*td\s*scorer|to\s+score(?:\s+a)?(?:\s+td)?|anytime\s+touchdown|touchdowns?|over|under)\b",
        " ",
        line,
    )
    text = re.sub(r"[-+]?\d+(?:\.\d+)?", " ", text)
    text = re.sub(r"[^A-Za-z.'\s]", " ", text)
    return " ".join(text.split())


def game_hits(overview: dict, stat_names: set[str], threshold: float, over: bool) -> tuple[int, int]:
    categories = (overview.get("gameLog") or {}).get("statistics") or []
    totals: dict[str, float] = {}
    for category in categories:
        names = category.get("names") or []
        indexes = [index for index, name in enumerate(names) if name in stat_names]
        if not indexes:
            continue
        for event in category.get("events") or []:
            event_id = str(event.get("eventId") or "")
            values = event.get("stats") or []
            if not event_id:
                continue
            added = sum(_number(values[index]) for index in indexes if index < len(values))
            totals[event_id] = totals.get(event_id, 0.0) + added
    if not totals:
        return 0, 0
    if over:
        hits = sum(1 for value in totals.values() if value > threshold)
    else:
        hits = sum(1 for value in totals.values() if value < threshold)
    return hits, len(totals)


def season_phrase(overview: dict, stat_names: set[str]) -> str:
    stats = overview.get("statistics") or {}
    if not isinstance(stats, dict):
        return ""
    keys = stats.get("names") or []
    split = next((row for row in stats.get("splits") or [] if row.get("displayName") == "Regular Season"), None)
    if split is None:
        return ""
    values = split.get("stats") or []
    bits = []
    for name in stat_names:
        if name in keys:
            bits.append(f"{_number(values[keys.index(name)]):.0f} {STAT_WORDS.get(name, name)}")
    if not bits:
        return ""
    return "Season " + ", ".join(bits) + ". "


def _hit_rate(hits: int, games: int) -> float:
    if hits <= 0:
        rate = 1.0 / (games + 2)
    else:
        rate = hits / games
    return min(max(rate, 0.03), 0.93)


def leg_from_overview(name: str, league: str, line: str, overview: dict) -> MarketLeg | None:
    market, stat_names, threshold, over, label = prop_spec(line, league)
    hits, games = game_hits(overview, stat_names, threshold, over)
    if games <= 0:
        return None
    rate = _hit_rate(hits, games)
    side = "YES" if market in {"ANYTIME_TD", "GOALSCORER"} else ("OVER" if over else "UNDER")
    if market in {"ANYTIME_TD", "GOALSCORER"}:
        happened = "Scored a TD" if market == "ANYTIME_TD" else "Scored a goal"
        detail = f"{happened} in {hits} of {games} games."
        selection = f"{name} {label}"
    else:
        word = "over" if over else "under"
        detail = f"{word.title()} {threshold:g} in {hits} of {games} games."
        selection = f"{name} {word.title()} {threshold:g} {label}"
    rationale = f"{name}, {league.upper()}. {season_phrase(overview, stat_names)}{detail}"
    if hits <= 0:
        rationale += " None of the logged games hit, so this chance stays small."
    decimal_odds = 1.0 / rate
    american = implied_to_american(rate)
    events = ((overview.get("nextGame") or {}).get("league") or {}).get("events") or []
    matchup = str(events[0].get("shortName") or league.upper()) if events else league.upper()
    digest = hashlib.sha1(f"{name}|{market}|{threshold}|{hits}|{games}".encode()).hexdigest()[:16]
    return MarketLeg(
        leg_id=f"stats-{digest}",
        game_id=f"stats-{digest}",
        matchup=matchup,
        game_date="",
        start_time_utc="",
        market_type=market,
        market_key=f"stats|{digest}",
        target_entity=name,
        selection=selection,
        side=side,
        line=threshold,
        american_odds=american,
        decimal_odds=decimal_odds,
        implied_prob=rate,
        fair_prob=rate,
        model_prob=rate,
        ev=0.0,
        bookmaker="stats",
        rationale=rationale,
        tags=[league.upper()],
    )


def _pick_athlete(items: list[dict], query: str, league_hint: str) -> dict | None:
    last = _last_name(query)
    if not last:
        return None
    ranked = []
    for item in items:
        if item.get("type") not in {None, "player"}:
            continue
        if _last_name(str(item.get("displayName") or "")) != last:
            continue
        league = str(item.get("league") or "")
        ranked.append((5 if league == league_hint else 0, item))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked[0][1]


def describe_player_leg(line: str) -> MarketLeg | None:
    """Look the player up and estimate how often this bet has hit."""
    if not is_player_line(line):
        return None
    query = player_query(line)
    if len(_last_name(query)) < 3:
        return None
    try:
        payload = get_json(SEARCH_URL, {"query": query, "limit": 5, "type": "player"}, ttl_seconds=3600)
    except Exception as exc:
        logger.warning("Player search failed for %s: %s", query, exc)
        return None
    athlete = _pick_athlete(payload.get("items") or [], query, _league_hint(line))
    if athlete is None or not athlete.get("id"):
        return None
    sport = str(athlete.get("sport") or "")
    league = str(athlete.get("league") or "")
    if not sport or not league:
        return None
    try:
        overview = get_json(
            OVERVIEW_URL.format(sport=sport, league=league, athlete_id=athlete["id"]),
            ttl_seconds=1800,
        )
    except Exception as exc:
        logger.warning("Player stats failed for %s: %s", query, exc)
        return None
    return leg_from_overview(str(athlete.get("displayName") or query), league, line, overview)


def use_book_price(stat: MarketLeg, board: MarketLeg) -> MarketLeg:
    """Keep the stats hit chance and the sportsbook's price when both exist."""
    return stat.model_copy(
        update={
            "american_odds": board.american_odds,
            "decimal_odds": board.decimal_odds,
            "implied_prob": board.implied_prob,
            "bookmaker": board.bookmaker,
            "matchup": board.matchup or stat.matchup,
            "game_id": board.game_id,
            "ev": expected_value(stat.model_prob, board.decimal_odds),
            "leg_id": board.leg_id,
        }
    )


def fair_decimal(probability: float) -> float:
    if probability <= 0:
        return math.inf
    return 1.0 / probability
