"""Turn the already-loaded Studio boards into OmniScreener quotes.

The Studio board keeps one price per bet (one book, Over side only for props), so for Deck D and
the Under rows this also reads the same SharpAPI pages again. Those pages are in the 15-minute
disk cache from the board load, so this normally costs no requests. Nothing here invents a price.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.api.data_models import PricedBoard
from src.engine.screener import Quote, model_probs_from_legs, quote_from_leg, quote_from_offer, row_key

logger = logging.getLogger(__name__)


@dataclass
class SportFeed:
    sport: str
    quotes: list[Quote] = field(default_factory=list)
    model_probs: dict = field(default_factory=dict)
    games: int = 0
    source: str = ""
    note: str = ""


def build_sport_feed(board: PricedBoard, sport: str, *, sharp_key: str = "", live: bool = True) -> SportFeed:
    """Quotes for one league: board legs (real and estimated) plus every real book's raw offers."""
    sport = sport.upper()
    feed = SportFeed(sport=sport, games=len(board.context.games), source=board.odds_source)
    seen: set[tuple] = set()

    def add(quote: Quote | None) -> None:
        if quote is None:
            return
        marker = (*row_key(quote), quote.book)
        if marker in seen:
            return
        seen.add(marker)
        feed.quotes.append(quote)

    for leg in [*board.legs, *board.prop_legs]:
        add(quote_from_leg(sport, leg))
    feed.model_probs = model_probs_from_legs(sport, [*board.legs, *board.prop_legs])

    if live and sharp_key and board.context.games:
        try:
            from src.api.sharp_client import fetch_sharp_offers

            offers = fetch_sharp_offers(board.context.games, board.context.players, sharp_key, sport, extended=True)
            for offer in offers:
                add(quote_from_offer(sport, offer))
            feed.note = str(getattr(fetch_sharp_offers, "last_note", "") or "").replace("Press Refresh slate", "Press Refresh feed")
        except Exception as exc:  # the board's own legs still give rows
            logger.warning("%s OmniScreener odds read failed: %s", sport, type(exc).__name__)
            feed.note = f"{sport}: could not read all books ({type(exc).__name__}). Showing the board prices only."
    return feed
