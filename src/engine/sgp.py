"""Same-game parlay search with bitmask pruning."""

from __future__ import annotations

from dataclasses import dataclass, field

from src.engine.correlation import pair_conflicts, ticket_conflicts
from src.engine.devig import combine_decimal, decimal_to_american, format_american

PLAYER_MARKETS = {
    "SOG",
    "POINTS",
    "GOALSCORER",
    "ANYTIME_TD",
    "PASS_YDS",
    "RUSH_YDS",
    "REC_YDS",
    "REBOUNDS",
    "ASSISTS",
    "THREES",
    "SAVES",
}


@dataclass
class BettingLeg:
    id: str
    label: str
    category: str
    entity_id: str
    stat_type: str
    side: str
    decimal_odds: float
    model_prob: float = 0.0
    family_bits: int = 0
    player_index: int = -1
    ban_mask: int = 0
    index: int = 0


@dataclass
class ParlayFilter:
    min_legs: int = 2
    max_legs: int = 4
    min_decimal: float | None = None
    max_decimal: float | None = None
    allowed_categories: set[str] | None = None
    include_leg_ids: set[str] = field(default_factory=set)
    exclude_leg_ids: set[str] = field(default_factory=set)
    prop_side: str = "ANY"
    max_legs_per_player: int = 2
    result_limit: int = 30


@dataclass
class SgpTicket:
    legs: list[BettingLeg]
    raw_decimal: float
    adjusted_decimal: float
    raw_american: int
    adjusted_american: int
    correlation: float
    notes: list[str]
    hit_prob: float = 0.0
    truncated: bool = False


def category_for(market_type: str) -> str:
    if market_type in {"MONEYLINE", "MONEYLINE_REG"}:
        return "MONEYLINE"
    if market_type in {"SPREAD", "PUCKLINE"}:
        return "SPREAD"
    if market_type in {"TOTAL", "TEAM_TOTAL"}:
        return "TOTAL"
    return "PLAYER_PROP"


def _family_key(leg) -> str:
    market = leg.market_type
    if market in {"MONEYLINE", "MONEYLINE_REG"}:
        return f"ML|{leg.game_id}"
    if market in {"SPREAD", "PUCKLINE"}:
        return f"SPREAD|{leg.game_id}|{leg.team_abbrev or leg.side}"
    if market == "TOTAL":
        return f"TOTAL|{leg.game_id}"
    if market == "TEAM_TOTAL":
        return f"TEAM_TOTAL|{leg.game_id}|{leg.team_abbrev}|{leg.line}"
    entity = leg.player_id or leg.target_entity
    return f"PROP|{leg.game_id}|{entity}|{market}"


def _opposite_key(leg) -> str | None:
    if leg.market_type in {"MONEYLINE", "MONEYLINE_REG", "TOTAL"} or leg.market_key:
        return f"MKT|{leg.market_key}"
    return None


def prepare_legs(raw_legs: list) -> list[BettingLeg]:
    """Assign family bits and direct bans before search."""
    family_ids: dict[str, int] = {}
    player_ids: dict[str, int] = {}
    prepared: list[BettingLeg] = []
    for raw in raw_legs:
        if not getattr(raw, "selectable", True):
            continue
        keys = [_family_key(raw)]
        opposite = _opposite_key(raw)
        if opposite:
            keys.append(opposite)
        bits = 0
        for key in keys:
            if key not in family_ids:
                family_ids[key] = len(family_ids)
            bits |= 1 << family_ids[key]
        player_index = -1
        if raw.market_type in PLAYER_MARKETS and (raw.player_id or raw.target_entity):
            player_key = str(raw.player_id or raw.target_entity)
            if player_key not in player_ids:
                player_ids[player_key] = len(player_ids)
            player_index = player_ids[player_key]
        prepared.append(
            BettingLeg(
                id=str(raw.leg_id),
                label=raw.selection,
                category=category_for(raw.market_type),
                entity_id=str(raw.player_id or raw.team_abbrev or raw.target_entity),
                stat_type=raw.market_type,
                side=raw.side,
                decimal_odds=float(raw.decimal_odds),
                model_prob=float(getattr(raw, "model_prob", 0) or (1.0 / float(raw.decimal_odds))),
                family_bits=bits,
                player_index=player_index,
            )
        )
    for index, leg in enumerate(prepared):
        leg.index = index
    by_id = {leg.id: leg for leg in prepared}
    raw_by_id = {str(leg.leg_id): leg for leg in raw_legs}
    for left in prepared:
        for right in prepared:
            if left.index >= right.index:
                continue
            if _same_team_ml_and_spread(raw_by_id[left.id], raw_by_id[right.id]) or pair_conflicts(
                raw_by_id[left.id], raw_by_id[right.id]
            ):
                left.ban_mask |= 1 << right.index
                right.ban_mask |= 1 << left.index
            if by_id[left.id].family_bits & by_id[right.id].family_bits:
                left.ban_mask |= 1 << right.index
                right.ban_mask |= 1 << left.index
    return prepared


def _same_team_ml_and_spread(left, right) -> bool:
    markets = {left.market_type, right.market_type}
    if "MONEYLINE" not in markets and "MONEYLINE_REG" not in markets:
        return False
    if not markets & {"SPREAD", "PUCKLINE"}:
        return False
    return bool(left.team_abbrev) and left.team_abbrev == right.team_abbrev and left.game_id == right.game_id


def _passes_static(leg: BettingLeg, options: ParlayFilter) -> bool:
    if leg.id in options.exclude_leg_ids:
        return False
    if options.allowed_categories and leg.category not in options.allowed_categories:
        return False
    if leg.category == "PLAYER_PROP" and options.prop_side == "OVER_ONLY" and leg.side not in {"OVER", "YES"}:
        return False
    if leg.category == "PLAYER_PROP" and options.prop_side == "UNDER_ONLY" and leg.side not in {"UNDER", "NO"}:
        return False
    return True


def correlation_adjustment(legs: list[BettingLeg]) -> tuple[float, list[str]]:
    """Books shorten stacks that move together and lengthen stacks that fight."""
    factor = 1.0
    notes: list[str] = []
    spreads = [leg for leg in legs if leg.category == "SPREAD"]
    totals = [leg for leg in legs if leg.category == "TOTAL"]
    moneylines = [leg for leg in legs if leg.category == "MONEYLINE"]
    props = [leg for leg in legs if leg.category == "PLAYER_PROP"]
    for spread in spreads:
        for total in totals:
            if spread.side == "HOME" and spread.stat_type and total.side == "OVER" and (spread.label.endswith("-") or "-" in spread.label.split()[-1]):
                factor *= 0.92
                notes.append("SGP haircut: spread and game over tend to hit together.")
                break
    favorite_ml = [leg for leg in moneylines if leg.decimal_odds < 2]
    if favorite_ml and any(leg.side == "UNDER" for leg in totals):
        factor *= 1.08
        notes.append("SGP boost: favorite moneyline with the under fights itself.")
    teams: dict[str, list[BettingLeg]] = {}
    for prop in props:
        teams.setdefault(prop.entity_id, []).append(prop)
    if any(len({leg.stat_type for leg in group}) >= 2 for group in teams.values()):
        factor *= 0.94
        notes.append("SGP haircut: same-player stats are correlated.")
    linked = {leg.entity_id for leg in props}
    if len(linked) >= 2 and any(leg.stat_type in {"PASS_YDS", "REC_YDS", "POINTS", "ASSISTS"} for leg in props):
        factor *= 0.93
        notes.append("SGP haircut: teammate props share the same scoring environment.")
    return min(max(factor, 0.75), 1.2), notes


def generate_valid_parlays(raw_legs: list, options: ParlayFilter | None = None) -> list[SgpTicket]:
    options = options or ParlayFilter()
    prepared = [leg for leg in prepare_legs(raw_legs) if _passes_static(leg, options)]
    by_id = {leg.id: leg for leg in prepared}
    missing = [leg_id for leg_id in options.include_leg_ids if leg_id not in by_id]
    if missing:
        return []
    anchors = [by_id[leg_id] for leg_id in options.include_leg_ids]
    anchor_mask = 0
    family_mask = 0
    player_counts = [0] * 64
    decimal = 1.0
    for anchor in anchors:
        if family_mask & anchor.family_bits or anchor_mask & anchor.ban_mask:
            return []
        if anchor.player_index >= 0:
            player_counts[anchor.player_index] += 1
            if player_counts[anchor.player_index] > options.max_legs_per_player:
                return []
        family_mask |= anchor.family_bits
        anchor_mask |= (1 << anchor.index) | anchor.ban_mask
        decimal *= anchor.decimal_odds
    pool = [leg for leg in prepared if leg.id not in options.include_leg_ids]
    raw_by_id = {str(leg.leg_id): leg for leg in raw_legs}
    found: list[SgpTicket] = []
    seen: set[tuple[str, ...]] = set()
    state = {"nodes": 0, "truncated": False}

    def record(chosen: list[BettingLeg]) -> None:
        if len(chosen) < options.min_legs:
            return
        if ticket_conflicts([raw_by_id[leg.id] for leg in chosen]):
            return
        raw = combine_decimal([leg.decimal_odds for leg in chosen])
        if options.min_decimal is not None and raw < options.min_decimal:
            return
        if options.max_decimal is not None and raw > options.max_decimal:
            return
        key = tuple(sorted(leg.id for leg in chosen))
        if key in seen:
            return
        seen.add(key)
        factor, notes = correlation_adjustment(chosen)
        adjusted = raw * factor
        hit = 1.0
        for leg in chosen:
            hit *= min(max(leg.model_prob, 0.0), 1.0)
        found.append(
            SgpTicket(
                legs=list(chosen),
                raw_decimal=raw,
                adjusted_decimal=adjusted,
                raw_american=decimal_to_american(raw),
                adjusted_american=decimal_to_american(adjusted),
                correlation=factor,
                notes=notes,
                hit_prob=hit,
            )
        )

    def dfs(start: int, chosen: list[BettingLeg], used_families: int, used_legs: int, counts: list[int], running: float) -> None:
        state["nodes"] += 1
        if state["nodes"] > 250_000:
            state["truncated"] = True
            return
        if len(chosen) >= options.min_legs:
            record(chosen)
        if len(chosen) >= options.max_legs or len(found) >= options.result_limit * 8:
            return
        for index in range(start, len(pool)):
            leg = pool[index]
            if used_families & leg.family_bits:
                continue
            if used_legs & leg.ban_mask:
                continue
            if leg.player_index >= 0 and counts[leg.player_index] >= options.max_legs_per_player:
                continue
            next_counts = counts
            if leg.player_index >= 0:
                next_counts = counts.copy()
                next_counts[leg.player_index] += 1
            chosen.append(leg)
            dfs(
                index + 1,
                chosen,
                used_families | leg.family_bits,
                used_legs | (1 << leg.index) | leg.ban_mask,
                next_counts,
                running * leg.decimal_odds,
            )
            chosen.pop()
            if state["truncated"]:
                return

    dfs(0, anchors, family_mask, anchor_mask, player_counts, decimal)
    found.sort(key=lambda ticket: ticket.adjusted_decimal)
    if state["truncated"]:
        for ticket in found:
            ticket.truncated = True
    return found[: options.result_limit]


def sample_matchup() -> list:
    """Tiny NBA card used by the unit test. Duck-typed like MarketLeg."""

    @dataclass
    class Sample:
        leg_id: str
        selection: str
        market_type: str
        market_key: str
        game_id: str
        team_abbrev: str | None
        player_id: int | None
        target_entity: str
        side: str
        line: float | None
        decimal_odds: float
        selectable: bool = True

    game = "nba-1"
    return [
        Sample("ml-h", "Celtics ML", "MONEYLINE", f"{game}|ML", game, "BOS", None, "Boston Celtics", "HOME", None, 1.55),
        Sample("ml-a", "Knicks ML", "MONEYLINE", f"{game}|ML", game, "NYK", None, "New York Knicks", "AWAY", None, 2.55),
        Sample("sp-h", "Celtics -4.5", "SPREAD", f"{game}|SPREAD|4.5", game, "BOS", None, "Boston Celtics", "HOME", -4.5, 1.91),
        Sample("sp-a", "Knicks +4.5", "SPREAD", f"{game}|SPREAD|4.5", game, "NYK", None, "New York Knicks", "AWAY", 4.5, 1.91),
        Sample("sp-h2", "Celtics -7.5", "SPREAD", f"{game}|SPREAD|7.5", game, "BOS", None, "Boston Celtics", "HOME", -7.5, 2.20),
        Sample("tot-o", "Over 224.5", "TOTAL", f"{game}|TOTAL|224.5", game, None, None, "BOS @ NYK", "OVER", 224.5, 1.91),
        Sample("tot-u", "Under 224.5", "TOTAL", f"{game}|TOTAL|224.5", game, None, None, "BOS @ NYK", "UNDER", 224.5, 1.91),
        Sample("pts", "Jayson Tatum Over 27.5", "POINTS", f"{game}|PTS|27.5", game, "BOS", 1, "Jayson Tatum", "OVER", 27.5, 1.87),
        Sample("pts-alt", "Jayson Tatum Over 31.5", "POINTS", f"{game}|PTS|31.5", game, "BOS", 1, "Jayson Tatum", "OVER", 31.5, 2.40),
        Sample("reb", "Jayson Tatum Over 8.5", "REBOUNDS", f"{game}|REB|8.5", game, "BOS", 1, "Jayson Tatum", "OVER", 8.5, 1.91),
        Sample("ast", "Jalen Brunson Over 7.5", "ASSISTS", f"{game}|AST|7.5", game, "NYK", 2, "Jalen Brunson", "OVER", 7.5, 1.83),
    ]


def describe_ticket(ticket: SgpTicket) -> str:
    names = ", ".join(leg.label for leg in ticket.legs)
    return (
        f"{names} | raw {format_american(ticket.raw_american)} | "
        f"SGP {format_american(ticket.adjusted_american)}"
    )
