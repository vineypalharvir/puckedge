"""100% hit-rate milestone scanner. Pure logic: no network and no Streamlit.

A milestone is an "X+" floor on a player's per-game stat (for example 10+ points). The scanner
steps down each stat's ladder and keeps every floor that was cleared in every game of a window:
the whole season so far, the last 10, or the last 5. A second mode finds floors that were cleared
in 9 of the last 10 games where the single miss fell short by exactly one unit.

Everything here works on per-game stat series (most recent game first). Prices come from real
sportsbook legs on the board and are never invented. Counts, percentages and cushions are never
negative, and a hit is a value that is at or above the floor (ties count).
"""

from __future__ import annotations

import itertools
import re
import unicodedata
from dataclasses import dataclass, field, replace
from typing import Iterable

from src.api.data_models import MarketLeg
from src.engine.correlation import ticket_conflicts
from src.engine.devig import combine_decimal, decimal_to_american, is_valid_american
from src.stats.books import SKIPPED_BOOKS

SEASON_MIN_GAMES = 5
L10_GAMES = 10
L5_GAMES = 5
HIGH_CUSHION = 1.5
BLOWOUT_SPREAD = 12.5
TOP_SUPPRESSORS = 3
SGP_LEGS = 3
SGP_LOW_AMERICAN = 150
SGP_HIGH_AMERICAN = 300
SGP_POOL = 18
SGP_CLOSEST = 3

SCOPES = ("season", "L10", "L5")
SCOPE_GAMES = {"L10": L10_GAMES, "L5": L5_GAMES}
SCOPE_WORDS = {"season": "this season", "L10": "the last 10 games", "L5": "the last 5 games"}


@dataclass(frozen=True)
class StatSpec:
    key: str
    label: str
    sport: str
    thresholds: tuple[int, ...]
    market: str = ""
    noun: str = ""


# The ladders are the ones in the feature request. `market` is the board market type used to find a
# real price. `noun` is how the stat reads in a defense note.
SPECS: dict[str, tuple[StatSpec, ...]] = {
    "NBA": (
        StatSpec("POINTS", "Points", "NBA", (10, 15, 20), "POINTS", "points"),
        StatSpec("ASSISTS", "Assists", "NBA", (2, 4, 6, 8), "ASSISTS", "assists"),
        StatSpec("REBOUNDS", "Rebounds", "NBA", (2, 4, 6, 8), "REBOUNDS", "rebounds"),
        StatSpec("THREES", "Threes", "NBA", (1, 2, 3), "THREES", "threes"),
    ),
    "NHL": (
        StatSpec("SOG", "SOG", "NHL", (2, 3), "SOG", "shots"),
        StatSpec("POINTS", "Points", "NHL", (1,), "POINTS", "goals"),
        StatSpec("HITS", "Hits", "NHL", (1, 2), "HITS", "hits"),
        StatSpec("BLOCKS", "Blocks", "NHL", (1,), "BLOCKS", "blocks"),
        StatSpec("SAVES", "Saves", "NHL", (20, 25), "SAVES", "shots"),
    ),
    "NFL": (
        StatSpec("RUSH_YDS", "Rushing Yards", "NFL", (40, 50), "RUSH_YDS", "rushing yards"),
        StatSpec("RECEPTIONS", "Receptions", "NFL", (3, 4), "RECEPTIONS", "receptions"),
        StatSpec("PASS_YDS", "Passing Yards", "NFL", (150, 200), "PASS_YDS", "passing yards"),
        StatSpec("PASS_TD", "Passing TDs", "NFL", (1,), "PASS_TD", "passing touchdowns"),
    ),
}


def specs_for(sport: str) -> tuple[StatSpec, ...]:
    return SPECS.get(str(sport or "").upper(), ())


def spec_for(sport: str, key: str) -> StatSpec | None:
    return next((spec for spec in specs_for(sport) if spec.key == key), None)


# --------------------------------------------------------------------------- names and ordinals

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def norm_name(name: str) -> str:
    """Lowercase ASCII name without punctuation or generational suffixes ("T.J." and "TJ" match)."""
    text = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    tokens = re.sub(r"[^a-z0-9 ]+", " ", text.lower().replace("'", "").replace(".", "")).split()
    kept = [token for token in tokens if token not in _SUFFIXES]
    return " ".join(kept)


def ordinal(number: int) -> str:
    if 10 <= number % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")
    return f"{number}{suffix}"


# --------------------------------------------------------------------------- data containers


@dataclass
class PlayerSeries:
    """One player's per-game stat values for the season, most recent game first."""

    name: str
    sport: str
    team: str
    opponent: str
    player_id: str = ""
    position: str = ""
    stats: dict[str, list[float]] = field(default_factory=dict)
    last_played: str = ""
    team_last_game: str = ""
    note: str = ""
    status: str = ""


@dataclass(frozen=True)
class DefenseRank:
    """Where a team sits among all teams at suppressing a stat. Rank 1 allows the least."""

    rank: int
    total: int
    value: float
    verb: str  # "allows" or "generates"
    noun: str
    unit: str = "per game"

    def phrase(self) -> str:
        place = "the fewest" if self.rank <= 1 else f"the {ordinal(self.rank)} fewest"
        return f"Opponent {self.verb} {place} {self.noun} per game ({self.value:.1f})"


@dataclass
class GameData:
    """Everything fetched for one game's card. Cached so switching modes never refetches."""

    sport: str
    game_id: str
    away: str
    home: str
    series: list[PlayerSeries] = field(default_factory=list)
    defense: dict[str, dict[str, DefenseRank]] = field(default_factory=dict)
    spread: float | None = None
    spread_source: str = ""
    status: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    excluded: list[str] = field(default_factory=list)
    loaded: int = 0
    total: int = 0
    partial: bool = False


@dataclass(frozen=True)
class PriceMatch:
    leg: MarketLeg
    exact: bool
    line: float
    milestone: int
    verified: bool
    book: str
    american: int
    note: str


@dataclass(frozen=True)
class HitRateProp:
    player: str
    player_id: str
    team: str
    opponent: str
    sport: str
    stat: str
    stat_label: str
    line: int
    scope: str
    hits: int
    games: int
    average: float
    cushion: float
    minimum: float
    last: float
    missed_by_one: bool = False
    miss_value: float | None = None
    season_hits: int = 0
    season_games: int = 0
    l5_hits: int = 0
    l5_games: int = 0
    price: PriceMatch | None = None
    traps: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    note: str = ""

    @property
    def hit_pct(self) -> float:
        return self.hits / self.games if self.games else 0.0

    @property
    def label(self) -> str:
        return f"{self.line}+ {self.stat_label}"

    @property
    def streak(self) -> str:
        return f"{self.hits}/{self.games}"

    @property
    def sample_text(self) -> str:
        return f"{self.hits}/{self.games} Games ({round(self.hit_pct * 100):d}%)"

    @property
    def high_cushion(self) -> bool:
        return self.cushion >= HIGH_CUSHION - 1e-9

    @property
    def is_trap(self) -> bool:
        return bool(self.traps)

    @property
    def addable(self) -> bool:
        return self.price is not None and (self.price.exact or self.price.verified)


# --------------------------------------------------------------------------- windows and ladder


def window_values(values: list[float], scope: str) -> list[float] | None:
    """The games a scope looks at, or None when the sample is too small to call anything 100%."""
    if not values:
        return None
    if scope == "season":
        return list(values) if len(values) >= SEASON_MIN_GAMES else None
    size = SCOPE_GAMES.get(scope)
    if size is None or len(values) < size:
        return None
    return list(values[:size])


def count_hits(values: Iterable[float], line: float) -> int:
    """Games at or above the floor. A tie with the floor is a hit."""
    return sum(1 for value in values if value >= line)


def ladder(values: list[float], thresholds: Iterable[int], scope: str) -> list[tuple[int, int, int]]:
    """(line, hits, games) for every rung, highest first. Empty when the window is too small."""
    window = window_values(values, scope)
    if window is None:
        return []
    return [(int(line), count_hits(window, line), len(window)) for line in sorted(set(thresholds), reverse=True)]


def best_full_rung(values: list[float], thresholds: Iterable[int], scope: str) -> tuple[int, int, int] | None:
    """The highest rung cleared in every game of the window."""
    for line, hits, games in ladder(values, thresholds, scope):
        if games and hits == games:
            return line, hits, games
    return None


def missed_by_one(values: list[float], line: float) -> float | None:
    """The one missed value when exactly 9 of the last 10 cleared and the miss was short by 1.

    Returns None for 10 of 10 (not a miss), 8 of 10, a miss by 2 or more, or fewer than 10 games.
    """
    window = window_values(values, "L10")
    if window is None:
        return None
    misses = [value for value in window if value < line]
    if len(misses) != 1:
        return None
    return misses[0] if abs((line - misses[0]) - 1.0) < 1e-9 else None


def _prop(series: PlayerSeries, spec: StatSpec, values: list[float], window: list[float], line: int, scope: str, *, miss: float | None = None) -> HitRateProp:
    average = sum(window) / len(window)
    return HitRateProp(
        player=series.name,
        player_id=series.player_id,
        team=series.team,
        opponent=series.opponent,
        sport=series.sport,
        stat=spec.key,
        stat_label=spec.label,
        line=line,
        scope=scope,
        hits=count_hits(window, line),
        games=len(window),
        average=average,
        cushion=max(average - line, 0.0),
        minimum=min(window),
        last=window[0],
        missed_by_one=miss is not None,
        miss_value=miss,
        season_hits=count_hits(values, line),
        season_games=len(values),
        l5_hits=count_hits(values[:5], line),
        l5_games=len(values[:5]),
        note=series.note,
    )


def scan_series(
    series: PlayerSeries,
    scope: str,
    *,
    extra: dict[str, Iterable[int]] | None = None,
    missed: bool = False,
) -> list[HitRateProp]:
    """Every qualifying rung for one player. `extra` adds rungs (for example the line a book posted)."""
    rows: list[HitRateProp] = []
    for spec in specs_for(series.sport):
        values = series.stats.get(spec.key)
        if not values:
            continue
        rungs = sorted({*spec.thresholds, *(int(line) for line in (extra or {}).get(spec.key, ()) if int(line) >= 1)}, reverse=True)
        if missed:
            window = window_values(values, "L10")
            if window is None:
                continue
            for line in rungs:
                miss = missed_by_one(values, line)
                if miss is not None:
                    rows.append(_prop(series, spec, values, window, line, "L10", miss=miss))
            continue
        window = window_values(values, scope)
        if window is None:
            continue
        for line in rungs:
            if count_hits(window, line) == len(window):
                rows.append(_prop(series, spec, values, window, line, scope))
    return rows


# --------------------------------------------------------------------------- traps


def rank_table(values: dict[str, float], *, verb: str, noun: str) -> dict[str, DefenseRank]:
    """Rank teams by a per-game number, lowest first. Ties share a rank."""
    clean = {team: float(value) for team, value in values.items() if value is not None and float(value) > 0}
    ranks: dict[str, DefenseRank] = {}
    for team, value in clean.items():
        better = sum(1 for other in clean.values() if other < value - 1e-9)
        ranks[team] = DefenseRank(rank=better + 1, total=len(clean), value=value, verb=verb, noun=noun)
    return ranks


def real_spread(legs: Iterable[MarketLeg], game_id: str) -> float | None:
    """The game's point spread from a real book, as a positive number. None when only estimates exist."""
    lines: list[float] = []
    for leg in legs:
        if leg.game_id != game_id or leg.market_type not in {"SPREAD", "PUCKLINE"}:
            continue
        if leg.bookmaker in SKIPPED_BOOKS or leg.line is None:
            continue
        lines.append(abs(float(leg.line)))
    return min(lines) if lines else None


def blowout_trap(spread: float | None) -> str | None:
    if spread is None:
        return None
    size = round(abs(float(spread)), 1)
    if size > BLOWOUT_SPREAD:
        return f"Spread is {size:g} points (over {BLOWOUT_SPREAD:g}): blowout risk, starters may be benched early"
    return None


def defense_trap(table: dict[str, dict[str, DefenseRank]] | None, stat: str, opponent: str) -> str | None:
    rank = ((table or {}).get(stat) or {}).get(opponent)
    if rank is not None and rank.rank <= TOP_SUPPRESSORS:
        return rank.phrase()
    return None


def trap_reasons(table: dict[str, dict[str, DefenseRank]] | None, stat: str, opponent: str, spread: float | None) -> tuple[str, ...]:
    reasons = [text for text in (defense_trap(table, stat, opponent), blowout_trap(spread)) if text]
    return tuple(reasons)


def defense_known(table: dict[str, dict[str, DefenseRank]] | None, stat: str, opponent: str) -> bool:
    return opponent in ((table or {}).get(stat) or {})


# --------------------------------------------------------------------------- prices

OfferIndex = dict[tuple[str, str], list[MarketLeg]]


def index_offers(legs: Iterable[MarketLeg], sport: str, game_id: str) -> OfferIndex:
    """Real OVER prices for this game by (normalized player, stat key)."""
    by_market: dict[str, list[str]] = {}
    for spec in specs_for(sport):
        if spec.market:
            by_market.setdefault(spec.market, []).append(spec.key)
    index: OfferIndex = {}
    for leg in legs:
        if leg.game_id != game_id or leg.bookmaker in SKIPPED_BOOKS or leg.line is None:
            continue
        if leg.side != "OVER" or not is_valid_american(leg.american_odds):
            continue
        for key in by_market.get(leg.market_type, ()):
            index.setdefault((norm_name(leg.target_entity), key), []).append(leg)
    return index


def offered_milestones(index: OfferIndex, player: str) -> dict[str, set[int]]:
    """Milestones that match real posted lines (a 2.5 line is the 3+ floor)."""
    found: dict[str, set[int]] = {}
    name = norm_name(player)
    for (who, key), legs in index.items():
        if who != name:
            continue
        for leg in legs:
            line = float(leg.line)
            if abs(line - round(line)) < 1e-9:
                continue
            found.setdefault(key, set()).add(int(line + 0.5))
    return found


def _pick_book(legs: list[MarketLeg], preferred: str) -> MarketLeg:
    chosen = next((leg for leg in legs if leg.bookmaker == preferred), None)
    return chosen or max(legs, key=lambda leg: leg.decimal_odds)


def match_price(
    index: OfferIndex,
    player: str,
    stat: str,
    line: int,
    window: list[float],
    preferred: str = "",
) -> PriceMatch | None:
    """A real posted price for this floor: the exact line first, else the nearest line above it.

    The X+ floor is the line X - 0.5. A nearer-above line is a harder bet, so it is only marked
    verified when the player also cleared that harder line in every game of the window.
    """
    legs = index.get((norm_name(player), stat)) or []
    if not legs:
        return None
    target = line - 0.5
    exact = [leg for leg in legs if abs(float(leg.line) - target) < 0.01]
    if exact:
        leg = _pick_book(exact, preferred)
        return PriceMatch(leg=leg, exact=True, line=float(leg.line), milestone=line, verified=True, book=leg.bookmaker, american=leg.american_odds, note="")
    above = [leg for leg in legs if float(leg.line) > target + 0.01]
    if not above:
        return None
    nearest = min(float(leg.line) for leg in above)
    leg = _pick_book([item for item in above if abs(float(item.line) - nearest) < 0.01], preferred)
    needed = int(nearest + 0.5)
    verified = bool(window) and count_hits(window, needed) == len(window)
    note = f"Priced line is Over {nearest:g} ({needed}+), a higher line than this {line}+ floor"
    return PriceMatch(leg=leg, exact=False, line=nearest, milestone=needed, verified=verified, book=leg.bookmaker, american=leg.american_odds, note=note)


# --------------------------------------------------------------------------- whole-game evaluation


def evaluate_game(
    data: GameData,
    legs: Iterable[MarketLeg],
    *,
    scope: str = "season",
    missed: bool = False,
    preferred: str = "",
    spread: float | None = None,
) -> list[HitRateProp]:
    """Every qualifying rung for every scanned player, with prices, traps and warnings attached."""
    legs = list(legs)
    index = index_offers(legs, data.sport, data.game_id)
    board_spread = real_spread(legs, data.game_id)
    game_spread = board_spread if board_spread is not None else (spread if spread is not None else data.spread)
    rows: list[HitRateProp] = []
    for series in data.series:
        extra = offered_milestones(index, series.name)
        found = scan_series(series, scope, extra=extra, missed=missed)
        for prop in found:
            window = window_values(series.stats.get(prop.stat) or [], prop.scope) or []
            price = match_price(index, series.name, prop.stat, prop.line, window, preferred)
            traps = trap_reasons(data.defense, prop.stat, series.opponent, game_spread)
            warnings = list(player_warnings(series))
            rows.append(replace(prop, price=price, traps=traps, warnings=tuple(warnings)))
    return rows


def player_warnings(series: PlayerSeries) -> list[str]:
    warnings: list[str] = []
    if series.status:
        warnings.append(f"Listed {series.status}")
    if series.last_played and series.team_last_game and series.last_played < series.team_last_game:
        warnings.append(f"Missed {series.team}'s last game (last played {series.last_played})")
    return warnings


def _sort_key(prop: HitRateProp) -> tuple:
    return (-prop.hit_pct, -prop.games, -prop.cushion, prop.player, -prop.line)


def display_rows(rows: list[HitRateProp]) -> list[HitRateProp]:
    """The highest rung per player and stat. When it has no usable price, the best priced lower rung is also shown."""
    grouped: dict[tuple[str, str, str], list[HitRateProp]] = {}
    for prop in rows:
        grouped.setdefault((prop.team, prop.player, prop.stat), []).append(prop)
    shown: list[HitRateProp] = []
    for group in grouped.values():
        group.sort(key=lambda prop: -prop.line)
        best = group[0]
        shown.append(best)
        if best.price is None or not best.addable:
            lower = next((prop for prop in group[1:] if prop.price is not None and prop.price.exact), None)
            if lower is not None:
                shown.append(lower)
    shown.sort(key=_sort_key)
    return shown


# --------------------------------------------------------------------------- undefeated SGP


@dataclass(frozen=True)
class SgpCombo:
    props: tuple[HitRateProp, ...]
    decimal: float
    american: int
    in_band: bool
    traps: int
    games: int
    cushion: float

    @property
    def legs(self) -> list[MarketLeg]:
        return [prop.price.leg for prop in self.props if prop.price is not None]


@dataclass(frozen=True)
class SgpResult:
    best: SgpCombo | None
    closest: tuple[SgpCombo, ...]
    safest: SgpCombo | None
    pool: int
    message: str


def sgp_eligible(prop: HitRateProp) -> bool:
    """100% props with an exact real price. Missed-by-one rows are never part of an undefeated SGP."""
    return prop.price is not None and prop.price.exact and prop.hits == prop.games and not prop.missed_by_one


def _prop_key(prop: HitRateProp) -> tuple:
    return (1 if prop.is_trap else 0, -prop.games, -prop.cushion, prop.player, prop.stat)


def _combo(props: tuple[HitRateProp, ...], low: int, high: int) -> SgpCombo:
    decimal = combine_decimal([prop.price.leg.decimal_odds for prop in props])
    american = decimal_to_american(decimal)
    return SgpCombo(
        props=props,
        decimal=decimal,
        american=american,
        in_band=low <= american <= high,
        traps=sum(1 for prop in props if prop.is_trap),
        games=sum(prop.games for prop in props),
        cushion=sum(prop.cushion for prop in props),
    )


def _combo_key(combo: SgpCombo) -> tuple:
    return (combo.traps, -combo.games, -combo.cushion, tuple(prop.player for prop in combo.props), tuple(prop.stat for prop in combo.props))


def _distance(combo: SgpCombo, low: int, high: int) -> float:
    """How far a combo's decimal price sits outside the band. 0 inside it."""
    low_dec = 1.0 + low / 100.0
    high_dec = 1.0 + high / 100.0
    if combo.decimal < low_dec:
        return low_dec - combo.decimal
    if combo.decimal > high_dec:
        return combo.decimal - high_dec
    return 0.0


def _price_text(american: int) -> str:
    return f"+{american}" if american > 0 else str(american)


def build_sgp(
    props: Iterable[HitRateProp],
    *,
    size: int = SGP_LEGS,
    low: int = SGP_LOW_AMERICAN,
    high: int = SGP_HIGH_AMERICAN,
    pool: int = SGP_POOL,
) -> SgpResult:
    """Pick the safest `size` 100% props from one game whose multiplied price lands in the band.

    Candidates need an exact real price. Non-trap props are preferred, then the larger sample, then
    the bigger cushion. Combos with contradictory legs are skipped (same rules as the slip). Prices
    are never padded: when no combo lands in the band the closest ones are returned with the reason.
    """
    eligible = sorted((prop for prop in props if sgp_eligible(prop)), key=_prop_key)
    candidates = eligible[:pool]
    if len(candidates) < size:
        return SgpResult(None, (), None, len(eligible), f"Only {len(eligible)} priced 100% props in this game; an undefeated SGP needs {size} with real prices.")
    combos: list[SgpCombo] = []
    for picked in itertools.combinations(candidates, size):
        if ticket_conflicts([prop.price.leg for prop in picked]):
            continue
        combos.append(_combo(picked, low, high))
    if not combos:
        return SgpResult(None, (), None, len(eligible), f"The priced 100% props in this game all conflict with each other, so no {size}-leg SGP can be built.")
    combos.sort(key=_combo_key)
    inside = [combo for combo in combos if combo.in_band]
    safest = combos[0]
    if inside:
        best = inside[0]
        return SgpResult(best, (), safest, len(eligible), f"Safest {size} priced 100% props landing at {_price_text(best.american)}.")
    ordered = sorted(combos, key=lambda combo: (_distance(combo, low, high), _combo_key(combo)))
    closest = tuple(ordered[:SGP_CLOSEST])
    nearest = closest[0]
    side = "below" if nearest.decimal < 1.0 + low / 100.0 else "above"
    bound = f"your {_price_text(low)} floor" if side == "below" else f"your {_price_text(high)} ceiling"
    message = (
        f"Safest {size} cost {_price_text(safest.american)}, outside the {_price_text(low)} to {_price_text(high)} band. "
        f"The closest combo is {_price_text(nearest.american)}, {side} {bound}. Nothing was padded to fit."
    )
    return SgpResult(None, closest, safest, len(eligible), message)


# --------------------------------------------------------------------------- slip bridge


def slip_quote(prop: HitRateProp) -> dict | None:
    """The slip entry for a priced prop, in the same shape the 'Add these legs to the slip' button uses.

    The leg's chance is the book's no-vig (fair) probability for the price that was posted, not the
    observed hit rate: a streak of 100% over a handful of games is evidence, not a 100% chance.
    """
    if prop.price is None or not prop.addable:
        return None
    leg = prop.price.leg
    probability = leg.fair_prob if 0.0 < leg.fair_prob < 1.0 else leg.implied_prob
    line = leg.line
    if prop.price.exact:
        selection = f"{prop.player} {prop.label}"
    else:
        selection = f"{prop.player} Over {leg.line:g} {prop.stat_label}"
    matchup = leg.matchup
    sport = (leg.sport or prop.sport).upper()
    season_rate = prop.season_hits / prop.season_games if prop.season_games else probability
    return {
        "id": leg.leg_id,
        "sport": sport,
        "player": prop.player,
        "selection": selection,
        "probability": probability,
        "season_hit": season_rate,
        "sample": f"{prop.sample_text} {SCOPE_WORDS.get(prop.scope, prop.scope)}",
        "l5": f"{prop.l5_hits} of {prop.l5_games} games",
        "season_avg": prop.average,
        "opponent": "",
        "american": leg.american_odds,
        "decimal": leg.decimal_odds,
        "book": leg.bookmaker,
        "best_note": "",
        "matchup": matchup,
        "game_id": leg.game_id,
        "rationale": f"100% hit-rate scanner: {prop.sample_text} {SCOPE_WORDS.get(prop.scope, prop.scope)}, average {prop.average:.1f}. Chance is the book's no-vig price, not the streak.",
        "game_key": f"{sport}|{matchup}" if matchup else "",
        "market_type": leg.market_type,
        "market_key": leg.market_key,
        "pick": "OVER",
        "line": line,
        "team": prop.team,
    }
