"""Hit rates counted from a game log. No odds and no network."""

from __future__ import annotations

from dataclasses import dataclass

from src.stats.catalog import Market


@dataclass(frozen=True)
class RateRow:
    key: str
    label: str
    side: str
    line: float | None
    season_avg: float | None
    l5_text: str
    season_text: str
    sample: str
    probability: float | None


def _number(value) -> float:
    cleaned = str(value).replace(",", "").replace("%", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def _expand_keys(stat_keys) -> tuple[str, ...]:
    aliases = {
        "rebounds": ("rebounds", "totalRebounds"),
        "threePointFieldGoalsMade": ("threePointFieldGoalsMade", "threePointFieldGoalsMade-threePointFieldGoalsAttempted"),
    }
    expanded = []
    for key in stat_keys:
        expanded.extend(aliases.get(key, (key,)))
    return tuple(dict.fromkeys(expanded))


def _cell(column: str, raw, wanted: tuple[str, ...]) -> float:
    text = str(raw).strip()
    if "-" in text and any(column.startswith(f"{key}-") for key in wanted):
        return _number(text.split("-", 1)[0])
    return _number(text)


def _merge_events(per_category: list[list[tuple[str, float]]]) -> list[float] | None:
    if not per_category:
        return None
    base = max(per_category, key=len)
    order = [event_id for event_id, _value in base]
    totals = {event_id: 0.0 for event_id in order}
    for events in per_category:
        for event_id, value in events:
            if event_id not in totals:
                order.append(event_id)
                totals[event_id] = 0.0
            totals[event_id] += value
    return [totals[event_id] for event_id in order]


def series_from_overview(overview: dict, stat_keys: tuple[str, ...] | set[str]) -> list[float] | None:
    """Per-game totals, most recent first. None when the log has none of these columns."""
    categories = (overview.get("gameLog") or {}).get("statistics") or []
    wanted = _expand_keys(stat_keys)
    per_category: list[list[tuple[str, float]]] = []
    for category in categories:
        names = category.get("names") or []
        indexes = [index for index, name in enumerate(names) if name in wanted or any(name.startswith(f"{key}-") for key in wanted)]
        if not indexes:
            continue
        events = []
        for event in category.get("events") or []:
            event_id = str(event.get("eventId") or "")
            if not event_id:
                continue
            values = event.get("stats") or []
            added = sum(_cell(names[index], values[index], wanted) for index in indexes if index < len(values))
            events.append((event_id, added))
        if events:
            per_category.append(events)
    return _merge_events(per_category)


def series_from_gamelog(payload: dict, stat_keys: tuple[str, ...] | set[str]) -> list[float] | None:
    """Regular-season rows from the full ESPN gamelog. Made-attempted cells count the makes."""
    if not isinstance(payload, dict) or not payload.get("seasonTypes"):
        return None
    seasons = payload.get("seasonTypes") or []

    def _regular(item: dict) -> bool:
        text = str(item.get("displayName") or "").lower()
        return "regular season" in text and "play" not in text

    regulars = [item for item in seasons if _regular(item)]
    season = regulars[0] if regulars else (seasons[0] if seasons else None)
    if season is None:
        return None
    wanted = _expand_keys(stat_keys)
    names = payload.get("names") or []
    per_category: list[list[tuple[str, float]]] = []
    for category in season.get("categories") or []:
        columns = category.get("names") or names
        indexes = [index for index, name in enumerate(columns) if name in wanted or any(name.startswith(f"{key}-") for key in wanted)]
        if not indexes:
            continue
        events = []
        for event in category.get("events") or []:
            event_id = str(event.get("eventId") or "")
            values = event.get("stats") or []
            if not event_id:
                continue
            added = sum(_cell(columns[index], values[index], wanted) for index in indexes if index < len(values))
            events.append((event_id, added))
        if events:
            per_category.append(events)
    values = _merge_events(per_category)
    return values


def series_from_nhl(payload: dict, key: str) -> list[float] | None:
    rows = list(payload.get("gameLog") or [])
    if not rows or not any(key in row and row.get(key) is not None for row in rows):
        return None
    rows.sort(key=lambda row: str(row.get("gameDate") or ""), reverse=True)
    return [float(row.get(key) or 0) for row in rows]


def _clears(value: float, side: str, line: float) -> bool:
    if side == "under":
        return value < line
    if side in {"milestone", "anytime"}:
        return value >= line
    return value > line


def _chance(hits: int, games: int) -> tuple[float, str]:
    if hits <= 0:
        return 1.0 / (games + 2), f"0 of {games} games"
    return hits / games, f"{hits} of {games} games"


def parse_custom_line(text: str, market_label: str) -> tuple[str, float, str] | None:
    """Turn 5+, 2.5+, or under 1.5 into a side, a number, and a label."""
    raw = text.strip().lower().replace(" ", "")
    if not raw:
        return None
    side = "milestone"
    if raw.startswith("under"):
        side = "under"
        raw = raw[5:]
    elif raw.startswith("over"):
        side = "over"
        raw = raw[4:]
    raw = raw.rstrip("+")
    try:
        line = float(raw)
    except ValueError:
        return None
    if side == "under":
        label = f"Under {line:g} {market_label}"
    elif side == "over":
        label = f"Over {line:g} {market_label}"
    else:
        label = f"{line:g}+ {market_label}"
    return side, line, label


def rate_row(market: Market, side: str, line: float | None, values: list[float] | None, label: str) -> RateRow:
    key = f"{market.key}|{side}|{line if line is not None else 'na'}"
    if not values:
        return RateRow(key, label, side, line, None, "no stat sample", "no stat sample", "no stat sample", None)
    games = len(values)
    recent = values[:5]
    if side == "info":
        average = sum(values) / games
        return RateRow(key, label, side, line, average, f"{len(recent)} games", f"{games} games", "season", None)
    threshold = 1.0 if side == "anytime" else float(line)
    season_hits = sum(1 for value in values if _clears(value, side, threshold))
    l5_hits = sum(1 for value in recent if _clears(value, side, threshold))
    probability, sample = _chance(season_hits, games)
    _l5_prob, l5_sample = _chance(l5_hits, len(recent))
    return RateRow(
        key=key,
        label=label,
        side=side,
        line=line,
        season_avg=sum(values) / games,
        l5_text=l5_sample,
        season_text=sample,
        sample=sample,
        probability=probability,
    )


def rows_for_market(market: Market, values: list[float] | None) -> list[RateRow]:
    if market.group == "team":
        return []
    rows: list[RateRow] = []
    if market.anytime:
        name = "Anytime touchdown" if market.sport == "NFL" else "Anytime goal" if market.key == "nhl_goals" else market.label
        rows.append(rate_row(market, "anytime", 1.0, values, name))
    for milestone in market.milestones:
        suffix = "touchdowns" if market.key == "nfl_td" else market.label
        rows.append(rate_row(market, "milestone", float(milestone), values, f"{milestone:g}+ {suffix}"))
    for line in market.lines:
        rows.append(rate_row(market, "over", float(line), values, f"Over {line:g} {market.label}"))
        rows.append(rate_row(market, "under", float(line), values, f"Under {line:g} {market.label}"))
    return rows


def same_game_note(legs: list[dict]) -> str:
    players = [str(leg.get("player") or "") for leg in legs if leg.get("player")]
    matchups = [str(leg.get("matchup") or "") for leg in legs if leg.get("matchup")]
    if len(players) != len(set(players)) or len(matchups) != len(set(matchups)):
        return "Same game. These are not independent, so the true chance can be higher or lower."
    return ""


def slip_chance(legs: list[dict]) -> float:
    total = 1.0
    for leg in legs:
        total *= float(leg["probability"])
    return total
