"""OmniScreener engine: one row per real bet on the board, hit rates, and the four decks.

Pure Python (no Streamlit, no network). The page feeds it prices and game logs and it
returns sorted rows, so every rule below is unit tested.

What the numbers mean (the page repeats these in captions):

* Best price: the highest decimal price any REAL book posted for that exact player, market,
  line and side. Synthetic (estimated) prices never count. A row that only has a synthetic
  price is kept as "est." (shown, not selectable, left out of decks, EV and badges).
* Win probability: the app's blended model probability when the Studio board has one for the
  leg (MarketLeg.model_prob), otherwise the no-vig market probability. The no-vig probability
  is each book's two-way price de-vigged (multiplicative), averaged over the books that posted
  both sides. When only one side exists it is implied / 1.045, the same rule price_offers uses.
* EV%: p * (decimal - 1) - (1 - p) per $1, with p = win probability at the best real price.
  A negative EV is never shown as a number or a badge, only a plain "-".
* Hit rate: how often the stat cleared the line in the last 10 games played (L10, at least
  MIN_GAMES games needed) and in the whole current regular season. Over and X+ clear when
  value > line; Under clears when value < line. It is never used as the probability in EV.
* Delta: L10 hit rate minus the implied probability of the best price, in percentage points.
* Cents: the gap between two American prices after removing the -100..+100 dead zone, so
  -105 versus +105 is 10 cents (not 210) and a gap is the same size on either side of even.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field, replace
from datetime import datetime

from src.api.data_models import MarketLeg, Offer
from src.engine.correlation import slip_conflict
from src.engine.devig import (
    american_to_decimal,
    american_to_implied,
    combine_decimal,
    decimal_to_american,
    format_american,
    implied_to_american,
    is_valid_american,
)

SPORTS = ("NHL", "NBA", "NFL")
SPORT_COLORS = {"NHL": "#38BDF8", "NBA": "#F59E0B", "NFL": "#10B981"}
SYNTHETIC_BOOKS = {"synthetic-bet365", "stats", "typed", "from stats"}
BOOK_PRIORITY = ("bet365", "draftkings", "fanduel", "pinnacle")
SHOP_BOOKS = ("bet365", "fanduel", "draftkings", "pinnacle")
BOOK_NAMES = {"bet365": "Bet365", "draftkings": "DraftKings", "fanduel": "FanDuel", "pinnacle": "Pinnacle"}

MIN_GAMES = 5  # fewer logged games than this and a hit rate is not shown
L10_WINDOW = 10
ANVIL_MIN_HIT = 0.80
ANVIL_ODDS = (-400, -125)
HOT_MIN_HIT = 0.60
HOT_ODDS = (100, 280)
STREAK_MIN_SEASON_GAMES = 5
SHOP_MIN_CENTS = 25.0
SHOP_MIN_UNITS = 1.0
SINGLE_SIDE_HOLD = 1.045

TEAM_TYPES = {"MONEYLINE", "PUCKLINE", "SPREAD", "TOTAL", "TEAM_TOTAL"}
OPPOSITE = {"OVER": "UNDER", "UNDER": "OVER", "HOME": "AWAY", "AWAY": "HOME"}

CATEGORIES = [
    "All Markets",
    "Points",
    "Rebounds",
    "Assists",
    "Threes",
    "Shots on Goal",
    "Rushing Yards",
    "Touchdowns",
    "Goals",
    "Goalie Saves",
    "Passing & Receiving",
    "Combos (PRA / Stocks / DD)",
    "Blocks & Steals",
    "Team Lines",
]


# --------------------------------------------------------------------------- markets


@dataclass(frozen=True)
class MarketSpec:
    market_type: str
    label: str  # "Anytime Goal", "Shots on Goal"
    short: str  # "SOG"
    category: str
    stats: tuple[str, ...] | None  # canonical game-log columns to add up; None = no free game log
    kind: str = "count"  # count | yes | double_double


def _specs() -> dict[tuple[str, str], MarketSpec]:
    table: dict[tuple[str, str], MarketSpec] = {}

    def add(sport: str, market_type: str, label: str, short: str, category: str, stats, kind: str = "count") -> None:
        table[(sport, market_type)] = MarketSpec(market_type, label, short, category, stats, kind)

    add("NHL", "SOG", "Shots on Goal", "SOG", "Shots on Goal", ("shots",))
    add("NHL", "POINTS", "Points", "Pts", "Points", ("points",))
    add("NHL", "ASSISTS", "Assists", "Ast", "Assists", ("assists",))
    add("NHL", "GOALSCORER", "Anytime Goal", "Goal", "Goals", ("goals",), "yes")
    add("NHL", "GOALS", "Goals", "Goals", "Goals", ("goals",))
    add("NHL", "SAVES", "Saves", "Saves", "Goalie Saves", ("saves",))
    add("NHL", "BLOCKS", "Blocked Shots", "Blk", "Blocks & Steals", None)
    add("NBA", "POINTS", "Points", "Pts", "Points", ("points",))
    add("NBA", "REBOUNDS", "Rebounds", "Reb", "Rebounds", ("rebounds",))
    add("NBA", "ASSISTS", "Assists", "Ast", "Assists", ("assists",))
    add("NBA", "THREES", "Threes", "3PM", "Threes", ("threes",))
    add("NBA", "STEALS", "Steals", "Stl", "Blocks & Steals", ("steals",))
    add("NBA", "BLOCKS", "Blocks", "Blk", "Blocks & Steals", ("blocks",))
    add("NBA", "PRA", "Pts + Reb + Ast", "PRA", "Combos (PRA / Stocks / DD)", ("points", "rebounds", "assists"))
    add("NBA", "STOCKS", "Steals + Blocks", "Stocks", "Combos (PRA / Stocks / DD)", ("steals", "blocks"))
    add("NBA", "DOUBLE_DOUBLE", "Double-Double", "DD", "Combos (PRA / Stocks / DD)", ("points", "rebounds", "assists", "steals", "blocks"), "double_double")
    add("NFL", "PASS_YDS", "Passing Yards", "Pass Yds", "Passing & Receiving", ("pass_yds",))
    add("NFL", "PASS_TD", "Passing TDs", "Pass TD", "Touchdowns", ("pass_td",))
    add("NFL", "RUSH_YDS", "Rushing Yards", "Rush Yds", "Rushing Yards", ("rush_yds",))
    add("NFL", "RUSH_ATT", "Rush Attempts", "Rush Att", "Rushing Yards", ("rush_att",))
    add("NFL", "REC_YDS", "Receiving Yards", "Rec Yds", "Passing & Receiving", ("rec_yds",))
    add("NFL", "RECEPTIONS", "Receptions", "Rec", "Passing & Receiving", ("receptions",))
    add("NFL", "ANYTIME_TD", "Anytime TD", "TD", "Touchdowns", ("rush_td", "rec_td"), "yes")
    return table


SPECS = _specs()
TEAM_LABELS = {
    "MONEYLINE": "Moneyline",
    "PUCKLINE": "Puck Line",
    "SPREAD": "Spread",
    "TOTAL": "Game Total",
    "TEAM_TOTAL": "Team Total",
}


def spec_for(sport: str, market_type: str) -> MarketSpec | None:
    return SPECS.get((sport.upper(), market_type.upper()))


def norm_name(name: str) -> str:
    return "".join(ch for ch in str(name or "").casefold() if ch.isalnum())


# --------------------------------------------------------------------------- odds helpers


def decimal_of(american: int) -> float:
    return american_to_decimal(american)


def in_odds_range(american: int, low: int | None, high: int | None) -> bool:
    """Inclusive range on American prices, compared as decimals so -125..+280 has no gap."""
    if not is_valid_american(american):
        return False
    value = american_to_decimal(american)
    if low is not None and value < american_to_decimal(low) - 1e-12:
        return False
    if high is not None and value > american_to_decimal(high) + 1e-12:
        return False
    return True


def cents_scale(american: int) -> float:
    """American price with the -100..+100 dead zone removed (-100 and +100 both become 0)."""
    value = float(american)
    return value - 100.0 if value > 0 else value + 100.0


def cents_gap(better: int, worse: int) -> float:
    """How many cents `better` pays over `worse` (negative when it pays less)."""
    return cents_scale(better) - cents_scale(worse)


def ev_fraction(win_prob: float, decimal_odds: float) -> float:
    return win_prob * (decimal_odds - 1.0) - (1.0 - win_prob)


def combined_odds(rows: list["ScreenerRow"]) -> tuple[int, float]:
    """(American, decimal) for a parlay of these rows: the product of their decimals."""
    if not rows:
        return 100, 1.0
    decimal = combine_decimal([row.decimal for row in rows])
    return decimal_to_american(decimal), decimal


def start_label(value: str) -> str:
    if not value:
        return "TBD"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    local = parsed.astimezone()
    return local.strftime("%I:%M %p").lstrip("0")


# --------------------------------------------------------------------------- player game logs


@dataclass
class PlayerSeries:
    """Per-game values for one player, most recent game first. All lists share one length."""

    sport: str
    name: str
    team: str = ""
    position: str = ""
    stats: dict[str, list[float]] = field(default_factory=dict)
    source: str = "ESPN game log, current regular season"

    @property
    def games(self) -> int:
        return min((len(values) for values in self.stats.values()), default=0)


def series_values(series: PlayerSeries | None, spec: MarketSpec | None) -> list[float] | None:
    """The per-game numbers a market is graded on, or None when this log cannot grade it."""
    if series is None or spec is None or spec.stats is None:
        return None
    lists = []
    for key in spec.stats:
        values = series.stats.get(key)
        if values is None:
            return None
        lists.append(values)
    games = min(len(values) for values in lists)
    if games <= 0:
        return None
    if spec.kind == "double_double":
        # points, rebounds, assists, steals, blocks: two or more at 10+ is a double-double.
        return [1.0 if sum(1 for values in lists if values[index] >= 10) >= 2 else 0.0 for index in range(games)]
    return [sum(values[index] for values in lists) for index in range(games)]


def clears(value: float, side: str, line: float) -> bool:
    if side == "UNDER":
        return value < line
    return value > line  # OVER, YES (line 0.5) and every X+ milestone (line X - 0.5)


@dataclass(frozen=True)
class HitStat:
    l10_hits: int
    l10_games: int
    season_hits: int
    season_games: int
    l10_avg: float
    season_avg: float


def compute_hit(values: list[float] | None, side: str, line: float | None) -> HitStat | None:
    if not values or line is None:
        return None
    window = values[:L10_WINDOW]
    return HitStat(
        l10_hits=sum(1 for value in window if clears(value, side, line)),
        l10_games=len(window),
        season_hits=sum(1 for value in values if clears(value, side, line)),
        season_games=len(values),
        l10_avg=sum(window) / len(window),
        season_avg=sum(values) / len(values),
    )


# --------------------------------------------------------------------------- quotes


@dataclass(frozen=True)
class Quote:
    """One real (or estimated) price from one book for one side of one bet."""

    sport: str
    game_id: str
    matchup: str
    start_utc: str
    market_type: str
    entity: str
    team: str
    side: str
    line: float | None
    american: int
    book: str
    selection: str = ""

    @property
    def synthetic(self) -> bool:
        return self.book in SYNTHETIC_BOOKS

    @property
    def is_team(self) -> bool:
        return self.market_type in TEAM_TYPES


def _line_key(line: float | None) -> str:
    return "na" if line is None else f"{float(line):.2f}"


def pair_id(q: Quote) -> tuple:
    """Everything two opposite sides of one bet share (so they can be de-vigged together)."""
    if q.market_type == "MONEYLINE":
        return (q.sport, q.game_id, q.market_type)
    if q.market_type in {"PUCKLINE", "SPREAD"}:
        home_line = (q.line or 0.0) if q.side == "HOME" else -(q.line or 0.0)
        return (q.sport, q.game_id, q.market_type, _line_key(home_line))
    if q.market_type == "TOTAL":
        return (q.sport, q.game_id, q.market_type, _line_key(q.line))
    return (q.sport, q.game_id, q.market_type, norm_name(q.entity), _line_key(q.line))


def row_key(q: Quote) -> tuple:
    return (*pair_id(q), q.side)


def quote_from_offer(sport: str, offer: Offer) -> Quote | None:
    sport = sport.upper()
    if not is_valid_american(offer.american_odds):
        return None
    market = offer.market_type.upper()
    side = offer.side.upper()
    if market in TEAM_TYPES:
        if side not in {"HOME", "AWAY", "OVER", "UNDER"}:
            return None
        if market in {"PUCKLINE", "SPREAD", "TOTAL", "TEAM_TOTAL"} and offer.line is None:
            return None
        return Quote(sport, offer.game_id, offer.matchup, offer.start_time_utc, market, offer.target_entity, offer.team_abbrev or "", side, offer.line, int(offer.american_odds), offer.bookmaker, offer.selection)
    spec = spec_for(sport, market)
    if spec is None:
        return None
    if spec.kind in {"yes", "double_double"}:
        if side not in {"YES", "OVER"}:
            return None
        side, line = "YES", 0.5
    else:
        if side not in {"OVER", "UNDER"} or offer.line is None:
            return None
        line = float(offer.line)
    return Quote(sport, offer.game_id, offer.matchup, offer.start_time_utc, market, offer.target_entity, offer.team_abbrev or "", side, line, int(offer.american_odds), offer.bookmaker, offer.selection)


def quote_from_leg(sport: str, leg: MarketLeg) -> Quote | None:
    offer = Offer(
        game_id=leg.game_id,
        matchup=leg.matchup,
        game_date=leg.game_date,
        start_time_utc=leg.start_time_utc,
        market_type=leg.market_type,
        market_key=leg.market_key,
        target_entity=leg.target_entity,
        player_id=leg.player_id,
        team_abbrev=leg.team_abbrev,
        opponent_abbrev=leg.opponent_abbrev,
        selection=leg.selection,
        side=leg.side,
        line=leg.line,
        american_odds=leg.american_odds,
        bookmaker=leg.bookmaker,
    )
    return quote_from_offer(sport, offer)


def model_probs_from_legs(sport: str, legs: list[MarketLeg]) -> dict[tuple, float]:
    """Studio's blended model probability per bet, only for legs the model really priced."""
    found: dict[tuple, float] = {}
    for leg in legs:
        if "blends" not in (leg.rationale or ""):
            continue
        quote = quote_from_leg(sport, leg)
        if quote is not None and not quote.synthetic:
            found[row_key(quote)] = float(leg.model_prob)
    return found


# --------------------------------------------------------------------------- rows


@dataclass(frozen=True)
class ScreenerRow:
    id: str
    sport: str
    game_id: str
    matchup: str
    start_utc: str
    start_text: str
    entity: str
    team: str
    is_team: bool
    market_type: str
    market_label: str
    category: str
    side: str
    line: float | None
    selection: str
    milestone: str
    price: int
    book: str
    books: dict[str, int]
    est: bool
    implied: float
    win_prob: float
    win_src: str  # "model", "market" or "est."
    fair_prob: float  # market no-vig only
    ev: float | None  # fraction of a $1 stake, None for est. rows
    supports_log: bool = False
    scanned: bool = False
    hit: HitStat | None = None
    cushion_l10: float | None = None
    cushion_season: float | None = None
    scan_team: str = ""
    flags: tuple[str, ...] = ()
    soft_note: str = ""

    # ---- derived numbers
    @property
    def decimal(self) -> float:
        return american_to_decimal(self.price)

    @property
    def matchup_label(self) -> str:
        return f"{self.matchup} • {self.start_text}" if self.matchup else self.start_text

    @property
    def l10_rate(self) -> float | None:
        if self.hit is None or self.hit.l10_games < MIN_GAMES:
            return None
        return self.hit.l10_hits / self.hit.l10_games

    @property
    def season_rate(self) -> float | None:
        if self.hit is None or self.hit.season_games < MIN_GAMES:
            return None
        return self.hit.season_hits / self.hit.season_games

    @property
    def delta_pp(self) -> float | None:
        rate = self.l10_rate
        return None if rate is None else (rate - self.implied) * 100.0

    @property
    def ev_pct(self) -> float | None:
        return None if self.ev is None else self.ev * 100.0

    @property
    def logo_team(self) -> str:
        return self.team or self.scan_team

    @property
    def selectable(self) -> bool:
        return not self.est

    def has_flag(self, name: str) -> bool:
        return name in self.flags

    # ---- slip
    def slip_quote(self) -> dict:
        """The same dict shape app.py's slip holds (see _quote / _team_quote there)."""
        pick = "YES" if self.side == "YES" else self.side
        sample = f"L10 {self.hit.l10_hits}/{self.hit.l10_games}" if self.l10_rate is not None and self.hit else "no game log scan"
        average = None if self.hit is None else self.hit.season_avg
        game_key = f"{self.sport}|{self.matchup}" if self.matchup else ""
        return {
            "id": f"omni|{self.id}",
            "sport": self.sport,
            "player": self.entity,
            "selection": self.selection,
            "probability": self.win_prob,
            "season_hit": self.season_rate if self.season_rate is not None else self.win_prob,
            "sample": sample,
            "l5": "—",
            "season_avg": average,
            "opponent": "",
            "american": self.price,
            "decimal": self.decimal,
            "book": self.book,
            "best_note": "",
            "matchup": self.matchup,
            "game_id": str(self.game_id),
            "rationale": f"OmniScreener: {self.selection} at {format_american(self.price)} ({BOOK_NAMES.get(self.book, self.book)}). Win chance {self.win_prob:.1%} ({self.win_src}).",
            "game_key": game_key,
            "market_type": self.market_type,
            "pick": pick,
            "line": self.line,
            "team": self.team if self.is_team and self.market_type != "TOTAL" else "",
        }

    def as_leg(self) -> MarketLeg:
        return MarketLeg(
            leg_id=self.id,
            game_id=self.game_id,
            matchup=self.matchup,
            game_date="",
            start_time_utc=self.start_utc,
            market_type=self.market_type,
            market_key=f"omni|{self.id}",
            target_entity=self.entity,
            team_abbrev=self.team or None,
            selection=self.selection,
            side=self.side,
            line=self.line,
            american_odds=self.price,
            decimal_odds=self.decimal,
            implied_prob=self.implied,
            fair_prob=self.fair_prob,
            model_prob=self.win_prob,
            ev=self.ev or 0.0,
            bookmaker=self.book,
            rationale="OmniScreener row",
            selectable=self.selectable,
            sport=self.sport,
        )


def _row_id(key: tuple) -> str:
    return hashlib.sha1("|".join(str(part) for part in key).encode()).hexdigest()[:14]


def _label(spec: MarketSpec | None, q: Quote) -> tuple[str, str, str, str]:
    """(selection label, milestone, market label, category)."""
    if q.is_team:
        return (q.selection or f"{q.entity} {TEAM_LABELS.get(q.market_type, q.market_type)}", "", TEAM_LABELS.get(q.market_type, q.market_type), "Team Lines")
    assert spec is not None
    if spec.kind in {"yes", "double_double"}:
        return f"{q.entity} {spec.label}", "", spec.label, spec.category
    word = "Over" if q.side == "OVER" else "Under"
    milestone = f"{int(math.floor(q.line or 0) + 1)}+" if q.side == "OVER" and q.line is not None and float(q.line) % 1 == 0.5 else ""
    return f"{q.entity} {word} {q.line:g} {spec.short}", milestone, spec.label, spec.category


def _market_fair(quotes_by_row: dict[tuple, list[Quote]], key: tuple, q: Quote) -> tuple[float, bool]:
    """(no-vig probability, was it a real two-way de-vig) for one side of one bet."""
    opposite_side = OPPOSITE.get(q.side)
    opposite_key = None
    if opposite_side is not None:
        opposite_key = (*key[:-1], opposite_side)
    mine: dict[str, int] = {}
    for item in quotes_by_row[key]:
        if not item.synthetic:
            mine[item.book] = item.american
    theirs: dict[str, int] = {}
    if opposite_key in quotes_by_row:
        for item in quotes_by_row[opposite_key]:
            if not item.synthetic:
                theirs[item.book] = item.american
    shares = []
    for book, price in mine.items():
        if book in theirs:
            own = american_to_implied(price)
            other = american_to_implied(theirs[book])
            shares.append(own / (own + other))
    if shares:
        return sum(shares) / len(shares), True
    singles = [min(american_to_implied(price) / SINGLE_SIDE_HOLD, 0.97) for price in mine.values()]
    return (sum(singles) / len(singles) if singles else 0.5), False


BOTTOM_RUNG = 0.5  # the lowest line on a count prop: books post it as "1+", usually Over only
MIN_BOOKS_FOR_BOTTOM_UNDER = 2


def bettable_quotes(quotes: list[Quote]) -> list[Quote]:
    """Drop player-prop Unders nobody will actually take a bet on.

    The odds feed lists some Unders a book shows in its data but does not offer (for example
    NHL "Under 0.5 assists"). Nothing in the feed marks those, so an Under is kept only when:

    * it is a real price (an estimated Under is never bettable),
    * the same book also posts the Over at that exact line (a real two-way market, not a stray
      one-sided row), and
    * on the bottom rung (line 0.5 or lower) at least two different books post that Under.

    Overs, Yes bets and team lines pass through unchanged. Dropping a fake Under also stops it
    from distorting the no-vig probability of the Over.
    """
    over_books: dict[tuple, set[str]] = {}
    under_books: dict[tuple, set[str]] = {}
    for q in quotes:
        if q.is_team or q.synthetic:
            continue
        if q.side == "OVER":
            over_books.setdefault(pair_id(q), set()).add(q.book)
        elif q.side == "UNDER":
            under_books.setdefault(pair_id(q), set()).add(q.book)
    kept: list[Quote] = []
    for q in quotes:
        if q.is_team or q.side != "UNDER":
            kept.append(q)
            continue
        if q.synthetic:
            continue
        pid = pair_id(q)
        if q.book not in over_books.get(pid, set()):
            continue
        if q.line is not None and q.line <= BOTTOM_RUNG and len(under_books.get(pid, set())) < MIN_BOOKS_FOR_BOTTOM_UNDER:
            continue
        kept.append(q)
    return kept


def build_rows(
    quotes: list[Quote],
    model_probs: dict[tuple, float] | None = None,
    series: dict[tuple[str, str], PlayerSeries | None] | None = None,
) -> list[ScreenerRow]:
    """Merge quotes into rows, price them, attach hit rates, and mark soft books."""
    model_probs = model_probs or {}
    series = series or {}
    grouped: dict[tuple, list[Quote]] = {}
    for quote in bettable_quotes(quotes):
        grouped.setdefault(row_key(quote), []).append(quote)
    rows: list[ScreenerRow] = []
    for key, items in grouped.items():
        real = {}
        for item in items:
            if item.synthetic:
                continue
            if item.book not in real or american_to_decimal(item.american) > american_to_decimal(real[item.book].american):
                real[item.book] = item
        est = not real
        if est:
            best = max(items, key=lambda item: american_to_decimal(item.american))
        else:
            best = max(
                real.values(),
                key=lambda item: (american_to_decimal(item.american), -BOOK_PRIORITY.index(item.book) if item.book in BOOK_PRIORITY else -99),
            )
        q = best
        spec = None if q.is_team else spec_for(q.sport, q.market_type)
        if not q.is_team and spec is None:
            continue
        selection, milestone, market_label, category = _label(spec, q)
        implied = american_to_implied(q.american)
        fair, _two_way = _market_fair(grouped, key, q)
        if est:
            win_prob, src = min(implied / SINGLE_SIDE_HOLD, 0.97), "est."
            ev = None
        else:
            if key in model_probs:
                win_prob, src = min(max(model_probs[key], 0.01), 0.99), "model"
            else:
                win_prob, src = fair, "market"
            ev = ev_fraction(win_prob, american_to_decimal(q.american))
        player_series = None if q.is_team else series.get((q.sport, norm_name(q.entity)))
        values = series_values(player_series, spec) if spec is not None else None
        hit = compute_hit(values, q.side, q.line)
        cushion_l10 = cushion_season = None
        if hit is not None and q.line is not None:
            sign = -1.0 if q.side == "UNDER" else 1.0
            cushion_l10 = sign * (hit.l10_avg - q.line)
            cushion_season = sign * (hit.season_avg - q.line)
        rows.append(
            ScreenerRow(
                id=_row_id(key),
                sport=q.sport,
                game_id=q.game_id,
                matchup=q.matchup,
                start_utc=q.start_utc,
                start_text=start_label(q.start_utc),
                entity=q.entity,
                team=q.team,
                is_team=q.is_team,
                market_type=q.market_type,
                market_label=market_label,
                category=category,
                side=q.side,
                line=q.line,
                selection=selection,
                milestone=milestone,
                price=q.american,
                book=q.book,
                books={book: item.american for book, item in real.items()},
                est=est,
                implied=implied,
                win_prob=win_prob,
                win_src=src,
                fair_prob=fair,
                ev=ev,
                supports_log=bool(spec is not None and spec.stats is not None),
                scanned=hit is not None and not est,
                hit=hit if not est else None,
                cushion_l10=cushion_l10 if not est else None,
                cushion_season=cushion_season if not est else None,
                scan_team=player_series.team if player_series is not None else "",
            )
        )
    return annotate_flags(rows)


# --------------------------------------------------------------------------- flags and decks


def streak_info(row: ScreenerRow) -> tuple[int, str, float | None] | None:
    """(games, badge text, cushion) for an unbroken 100% cash rate, else None."""
    hit = row.hit
    if row.est or hit is None:
        return None
    if hit.season_games >= STREAK_MIN_SEASON_GAMES and hit.season_hits == hit.season_games:
        return hit.season_games, f"{hit.season_games}/{hit.season_games} Games", row.cushion_season
    if hit.l10_games == L10_WINDOW and hit.l10_hits == hit.l10_games:
        return hit.l10_games, f"{hit.l10_games}/{hit.l10_games} Games (L10)", row.cushion_l10
    return None


def is_floor(row: ScreenerRow) -> bool:
    return streak_info(row) is not None


def is_hot(row: ScreenerRow) -> bool:
    rate = row.l10_rate
    return not row.est and rate is not None and rate >= HOT_MIN_HIT - 1e-9 and in_odds_range(row.price, *HOT_ODDS)


def annotate_flags(rows: list[ScreenerRow]) -> list[ScreenerRow]:
    soft = {item.pick.id: item for item in deck_line_shop(rows) if item.flagged}
    flagged: list[ScreenerRow] = []
    for row in rows:
        flags = []
        if is_hot(row):
            flags.append("hot_odds")
        if is_floor(row):
            flags.append("floor_100")
        if row.ev is not None and row.ev > 0:
            flags.append("plus_ev")
        shop = soft.get(row.id)
        if shop is not None:
            flags.append("soft_book")
        flagged.append(replace(row, flags=tuple(flags), soft_note=shop.reason if shop else ""))
    return flagged


def _meets(rate: float | None, minimum: float) -> bool:
    return rate is not None and round(rate, 9) >= minimum - 1e-9


def deck_anvil(rows: list[ScreenerRow]) -> list[ScreenerRow]:
    """Most likely to hit: L10 >= 80%, price -400..-125, highest win probability first."""
    picked = [row for row in rows if not row.est and _meets(row.l10_rate, ANVIL_MIN_HIT) and in_odds_range(row.price, *ANVIL_ODDS)]
    return sorted(picked, key=lambda row: (-row.win_prob, -(row.l10_rate or 0.0), row.id))


def deck_hot_odds(rows: list[ScreenerRow]) -> list[ScreenerRow]:
    """Plus money +100..+280 with L10 >= 60%, best EV% first, then hit-rate-over-price delta."""
    picked = [row for row in rows if not row.est and _meets(row.l10_rate, HOT_MIN_HIT) and in_odds_range(row.price, *HOT_ODDS)]
    return sorted(picked, key=lambda row: (-(row.ev if row.ev is not None else -9.0), -(row.delta_pp or -999.0), row.id))


def deck_streaks(rows: list[ScreenerRow]) -> list[ScreenerRow]:
    """Strict 100%: every game cleared (season sample >= 5, or exactly 10 of 10 in the L10)."""
    picked = [row for row in rows if streak_info(row) is not None]
    return sorted(picked, key=lambda row: (-streak_info(row)[0], -(streak_info(row)[2] or 0.0), row.id))


@dataclass(frozen=True)
class ShopRow:
    sport: str
    matchup_label: str
    entity: str
    selection: str  # label of the pick row
    side: str
    rows: tuple[ScreenerRow, ...]  # every real line for this player, market and side
    pick: ScreenerRow
    books: tuple[str, ...]
    price_cents: float  # best price vs the worst book's price on the same line
    fair_cents: float  # best price vs the no-vig consensus price
    unit_gap: float  # biggest line difference between books on the same side
    soft_book: str
    reason: str

    @property
    def flagged(self) -> bool:
        return bool(self.reason)

    @property
    def edge(self) -> float:
        """Consensus EV of the best price: no-vig probability x best decimal price - 1 (never negative)."""
        return max(0.0, self.pick.fair_prob * self.pick.decimal - 1.0)


def _fair_american(probability: float) -> int:
    return implied_to_american(probability)


def deck_line_shop(rows: list[ScreenerRow]) -> list[ShopRow]:
    """Rows with prices from at least 2 real books, with the 25-cent / 1-unit value flags.

    Flag (any one of): (a) on one line the best real price beats another book's price by
    >= 25 cents, (b) the best real price beats the no-vig consensus price by >= 25 cents,
    (c) two books post the same side at lines >= 1.0 stat unit apart (the friendlier line
    is the soft one: lower for Over/X+, higher for Under). Cents are defined at the top of
    this module. Only books that actually returned a price count.
    """
    groups: dict[tuple, list[ScreenerRow]] = {}
    for row in rows:
        if row.est:
            continue
        key = (row.sport, row.game_id, row.market_type, norm_name(row.entity), row.side)
        groups.setdefault(key, []).append(row)
    shops: list[ShopRow] = []
    for items in groups.values():
        books = sorted({book for row in items for book in row.books}, key=lambda b: BOOK_PRIORITY.index(b) if b in BOOK_PRIORITY else 99)
        if len(books) < 2:
            continue
        best_price_cents = 0.0
        best_fair_cents = 0.0
        pick = max(items, key=lambda row: (len(row.books), row.win_prob))
        pick_cents = -1.0
        soft_book = ""
        reasons: list[str] = []
        for row in items:
            if len(row.books) >= 2:
                prices = sorted(row.books.items(), key=lambda pair: american_to_decimal(pair[1]), reverse=True)
                gap = cents_gap(prices[0][1], prices[-1][1])
                fair_gap = cents_gap(prices[0][1], _fair_american(row.fair_prob))
                if gap > best_price_cents:
                    best_price_cents = gap
                if fair_gap > best_fair_cents:
                    best_fair_cents = fair_gap
                top = max(gap, fair_gap)
                if top >= SHOP_MIN_CENTS and top > pick_cents:
                    pick, pick_cents, soft_book = row, top, prices[0][0]
        lines: dict[str, float] = {}
        for row in items:
            if row.line is None or row.side == "YES":
                continue
            for book in row.books:
                lines[book] = row.line
        unit_gap = 0.0
        line_book = ""
        if len(lines) >= 2:
            unit_gap = max(lines.values()) - min(lines.values())
            if unit_gap >= SHOP_MIN_UNITS - 1e-9:
                friendly = min(lines, key=lambda b: lines[b]) if items[0].side == "OVER" else max(lines, key=lambda b: lines[b])
                line_book = friendly
        if best_price_cents >= SHOP_MIN_CENTS or best_fair_cents >= SHOP_MIN_CENTS:
            reasons.append(f"{BOOK_NAMES.get(soft_book, soft_book)} pays {max(best_price_cents, best_fair_cents):.0f} cents more")
        if unit_gap >= SHOP_MIN_UNITS - 1e-9 and line_book:
            reasons.append(f"{BOOK_NAMES.get(line_book, line_book)} has a line {unit_gap:g} unit(s) friendlier")
            if not soft_book:
                soft_book = line_book
                friendly_rows = [row for row in items if line_book in row.books and row.line is not None]
                if friendly_rows:
                    pick = friendly_rows[0]
        shops.append(
            ShopRow(
                sport=pick.sport,
                matchup_label=pick.matchup_label,
                entity=pick.entity,
                selection=pick.selection,
                side=pick.side,
                rows=tuple(sorted(items, key=lambda row: row.line or 0.0)),
                pick=pick,
                books=tuple(books),
                price_cents=best_price_cents,
                fair_cents=best_fair_cents,
                unit_gap=unit_gap,
                soft_book=soft_book,
                reason="; ".join(reasons),
            )
        )
    # Flagged first. Raw cents mean little on longshots (+7000 vs +10000 is 3000 "cents" and almost no
    # value), so rank by how much the best price beats the no-vig consensus probability, then line gap.
    return sorted(shops, key=lambda shop: (not shop.flagged, -shop.edge, -shop.unit_gap, shop.pick.id))


# --------------------------------------------------------------------------- filters, scan queue, slip


def row_matches_search(row: ScreenerRow, text: str) -> bool:
    tokens = [token for token in str(text or "").casefold().split() if token]
    if not tokens:
        return True
    hay = " ".join([row.entity, row.team, row.matchup, row.market_label, row.selection, row.sport]).casefold()
    return all(token in hay for token in tokens)


def filter_rows(
    rows: list[ScreenerRow],
    *,
    sport: str = "ALL",
    category: str = "All Markets",
    search: str = "",
    min_hit: float = 0.5,
    odds_low: int | None = None,
    odds_high: int | None = None,
) -> list[ScreenerRow]:
    """Quick-filter bar. Rows without a hit rate pass only while the slider sits at its minimum."""
    kept = []
    for row in rows:
        if sport != "ALL" and row.sport != sport:
            continue
        if category != "All Markets" and row.category != category:
            continue
        if not row_matches_search(row, search):
            continue
        if not in_odds_range(row.price, odds_low, odds_high):
            continue
        if min_hit > 0.5 + 1e-9:
            if not _meets(row.l10_rate, min_hit):
                continue
        kept.append(row)
    return kept


def scan_targets(rows: list[ScreenerRow], series: dict, attempts: dict | None = None, limit: int = 25) -> list[tuple[str, str]]:
    """Players worth a game-log call next: closest to the Anvil / Hot Odds price bands first."""
    attempts = attempts or {}
    weight: dict[tuple[str, str], list] = {}
    names: dict[tuple[str, str], str] = {}
    for row in rows:
        if row.est or row.is_team or not row.supports_log:
            continue
        key = (row.sport, norm_name(row.entity))
        if key in series or attempts.get(key, 0) >= 2:
            continue
        in_band = in_odds_range(row.price, *ANVIL_ODDS) or in_odds_range(row.price, *HOT_ODDS)
        entry = weight.setdefault(key, [0, 0])
        entry[0] = max(entry[0], 2 if in_band else 1)
        entry[1] += 1
        names[key] = row.entity
    ordered = sorted(weight, key=lambda key: (-weight[key][0], -weight[key][1], key))
    return [(key[0], names[key]) for key in ordered[:limit]]


def scan_counts(rows: list[ScreenerRow]) -> tuple[int, int, int]:
    """(scanned, scannable, without a free game log) among real player rows."""
    scannable = [row for row in rows if not row.est and not row.is_team and row.supports_log]
    scanned = sum(1 for row in scannable if row.scanned)
    no_log = sum(1 for row in rows if not row.est and not row.is_team and not row.supports_log)
    return scanned, len(scannable), no_log


def selection_conflict(rows: list[ScreenerRow], existing: list[dict]) -> str | None:
    """Plain-language reason these rows cannot all join the slip (None when they fit)."""
    others = list(existing)
    for row in rows:
        quote = row.slip_quote()
        if any(item.get("id") == quote["id"] for item in others):
            continue
        problem = slip_conflict(quote, others)
        if problem:
            return problem
        others.append(quote)
    return None
