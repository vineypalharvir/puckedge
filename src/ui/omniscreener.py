"""The OmniScreener page: a searchable feed of NHL, NBA and NFL lines with four decks.

Everything the page shows is built from rows in src/engine/screener.py. This module only
lays them out, keeps the checkbox selection, and adds legs to the same session slip the Studio
page uses (st.session_state["slip"], same dict shape, same conflict check).
"""

from __future__ import annotations

import html

import streamlit as st

from src.api.data_models import PricedBoard
from src.engine import screener as sc
from src.engine.devig import combine_decimal, decimal_to_american, format_american
from src.services.screener_feed import SportFeed, build_sport_feed
from src.stats import scan
from src.utils.team_assets import team_logo_html

NAV_STUDIO = "⚡ Studio & Scoreboard"
NAV_OMNI = "📡 OmniScreener Master Feed"
NAV_VAULT = "📊 Vault & Ledger"
NAV_SETUP = "Setup"
NAV_OPTIONS = [NAV_STUDIO, NAV_OMNI, NAV_VAULT, NAV_SETUP]

DECK_ANVIL = "The Anvil"
DECK_HOT = "Hot Odds"
DECK_STREAKS = "Unbroken Streaks"
DECK_SHOP = "Line-Shop Radar"
DECK_FEED = "Full Feed"
DECKS = [DECK_ANVIL, DECK_HOT, DECK_STREAKS, DECK_SHOP, DECK_FEED]
DECK_ICONS = {DECK_ANVIL: "🛡️ The Anvil", DECK_HOT: "🔥 Hot Odds", DECK_STREAKS: "🧊 Unbroken Streaks", DECK_SHOP: "🔎 Line-Shop Radar", DECK_FEED: "📡 Full Feed"}
DECK_BLURB = {
    DECK_ANVIL: "Safest anchors: last-10 hit rate 80% or better at -400 to -125, highest win probability first.",
    DECK_HOT: "Plus money (+100 to +280) where the last-10 hit rate is 60% or better, best EV% first.",
    DECK_STREAKS: "Iron floor: every game cleared (season sample of 5+ games, or 10 of 10 in the last 10).",
    DECK_SHOP: "Real books side by side. Flagged when a book pays 25+ cents more or posts a line a full unit friendlier.",
    DECK_FEED: "Every priced line, scanned or not. Unscanned rows show — instead of a hit rate.",
}
PAGE_SIZES = [25, 50, 100]
ODDS_STEPS = [-1000, -500, -400, -300, -250, -200, -175, -150, -125, -110, 100, 110, 125, 150, 175, 200, 250, 300, 400, 500, 750, 1000, 2000]
MAX_AUTO_STEPS = 12  # auto-scan reruns in a row before it waits for the next press

ODDS_NOTE = "n/a (not on your plan)"

CSS = """
<style>
.om-title { font-size: 1.5rem; font-weight: 800; letter-spacing: 0.04em; color: #FFFFFF; margin: 0.2rem 0 0.1rem 0; }
.om-title span { color: #9333EA; }
.om-sub { color: #9CA3AF; font-size: 0.78rem; }
.om-grid { display: grid; grid-template-columns: 52px 128px minmax(150px, 2fr) 100px 128px 108px minmax(110px, 1.4fr); gap: 8px; align-items: center; }
.om-grid-shop { display: grid; grid-template-columns: 48px 112px minmax(130px, 2fr) 76px 76px 96px 96px 70px minmax(110px, 1.5fr); gap: 8px; align-items: center; }
.om-head { color: #9CA3AF; font-size: 0.7rem; font-weight: 700; letter-spacing: 0.08em; text-transform: uppercase; padding: 0.35rem 0.5rem; border-bottom: 1px solid #1F1F28; }
.om-row { background: #121216; border: 1px solid #1F1F28; border-radius: 10px; padding: 0.4rem 0.5rem; min-height: 52px; }
.om-row:hover { border-color: #3B2A5A; }
.om-cell { min-width: 0; overflow-wrap: anywhere; font-size: 0.82rem; color: #E5E7EB; line-height: 1.25; }
.om-cell b { color: #FFFFFF; }
.om-muted { color: #6B7280; font-size: 0.72rem; }
.om-sport { display: inline-block; padding: 2px 9px; border-radius: 999px; font-size: 0.72rem; font-weight: 800; letter-spacing: 0.05em; }
.om-nhl { background: rgba(56, 189, 248, 0.15); color: #38BDF8; border: 1px solid rgba(56, 189, 248, 0.45); }
.om-nba { background: rgba(245, 158, 11, 0.15); color: #F59E0B; border: 1px solid rgba(245, 158, 11, 0.45); }
.om-nfl { background: rgba(16, 185, 129, 0.15); color: #10B981; border: 1px solid rgba(16, 185, 129, 0.45); }
.om-bar { height: 6px; background: #1F1F28; border-radius: 4px; overflow: hidden; margin-top: 4px; }
.om-bar i { display: block; height: 100%; background: #9333EA; border-radius: 4px; }
.om-chip { display: inline-block; padding: 1px 8px; margin: 1px 3px 1px 0; border-radius: 999px; font-size: 0.68rem; font-weight: 700; background: #1F1F28; color: #E5E7EB; white-space: nowrap; }
.om-chip.hot { background: #3F1D0B; color: #FDBA74; }
.om-chip.floor { background: #0C2A3A; color: #7DD3FC; }
.om-chip.ev { background: #052E16; color: #86EFAC; }
.om-chip.soft { background: #2E1065; color: #D8B4FE; white-space: normal; border-radius: 10px; }
.om-chip.est { background: #1F1F28; color: #9CA3AF; }
.om-logo { width: 22px; height: 22px; vertical-align: middle; margin-right: 6px; object-fit: contain; }
.om-streak { display: inline-block; padding: 2px 9px; border-radius: 8px; font-weight: 800; font-size: 0.78rem; background: #0C2A3A; color: #7DD3FC; border: 1px solid #155E75; }
.om-na { color: #6B7280; font-size: 0.68rem; }
.om-best { color: #86EFAC; font-weight: 800; }
div.st-key-omni-grid { overflow-x: auto; }
div.st-key-omni-grid > * { min-width: 980px; }
div.st-key-omni-shop { overflow-x: auto; }
div.st-key-omni-shop > * { min-width: 1100px; }
@media (max-width: 1150px) {
  div.st-key-omni-filters [data-testid="stHorizontalBlock"], div.st-key-omni-scan [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
  div.st-key-omni-filters [data-testid="stColumn"], div.st-key-omni-scan [data-testid="stColumn"] { min-width: 230px; flex: 1 1 230px; }
  div.st-key-omni-filters [data-testid="stColumn"]:first-child { flex-basis: 380px; }
}
[data-testid="stMainBlockContainer"] { max-width: 1480px; }
.om-pick-head { padding-left: 0; padding-right: 0; text-align: center; letter-spacing: 0; }
div.st-key-omni-filters, div.st-key-omni-decks, div.st-key-omni-scan { background: #121216; border: 1px solid #1F1F28; border-radius: 12px; padding: 0.6rem 0.8rem; }
div.st-key-omni-drawer { position: fixed; bottom: 0.7rem; left: 50%; transform: translateX(-50%); width: min(1080px, 96vw); z-index: 1000; background: rgba(18, 18, 22, 0.98); border: 1px solid #9333EA; border-radius: 14px; padding: 0.55rem 1rem; box-shadow: 0 -6px 28px rgba(147, 51, 234, 0.35); }
div.st-key-omni-drawer p { margin: 0; }
</style>
"""

NAV_CSS = """
<style>
div.st-key-nav_page [role="radiogroup"] { gap: 6px; flex-wrap: wrap; }
div.st-key-nav_page label[data-baseweb="radio"] { background: #121216; border: 1px solid #1F1F28; border-radius: 10px; padding: 6px 14px; margin: 0; cursor: pointer; }
div.st-key-nav_page label[data-baseweb="radio"] > div:first-child { display: none; }
div.st-key-nav_page label[data-baseweb="radio"]:has(input:checked) { background: #9333EA; border-color: #9333EA; }
div.st-key-nav_page label[data-baseweb="radio"]:has(input:checked) p { color: #FFFFFF; font-weight: 700; }
div.st-key-nav_page label[data-baseweb="radio"] p { font-size: 0.88rem; }
</style>
"""

DRAWER_PAD = "<style>.main .block-container, [data-testid='stMainBlockContainer'] { padding-bottom: 8rem !important; }</style>"


# --------------------------------------------------------------------------- slip glue


def _slip() -> list[dict]:
    return st.session_state.setdefault("slip", [])


def _notice(kind: str, text: str) -> None:
    st.session_state.setdefault("omni_notices", []).append((kind, text))


def add_rows_to_slip(rows: list[sc.ScreenerRow]) -> tuple[int, str | None]:
    """Add rows to the shared slip. Contradictory picks add nothing and return the reason."""
    chosen = [row for row in rows if row.selectable]
    slip = _slip()
    fresh = [row for row in chosen if not any(item.get("id") == f"omni|{row.id}" for item in slip)]
    if not fresh:
        return 0, None
    problem = sc.selection_conflict(fresh, slip)
    if problem:
        return 0, problem
    for row in fresh:
        slip.append(row.slip_quote())
    return len(fresh), None


def _rows_by_id() -> dict[str, sc.ScreenerRow]:
    return st.session_state.get("omni_rows_by_id", {})


def _selected_rows() -> list[sc.ScreenerRow]:
    chosen = st.session_state.get("omni_selected", {})
    lookup = _rows_by_id()
    return [lookup[rid] for rid in chosen if rid in lookup and lookup[rid].selectable]


def _on_pick(rid: str) -> None:
    chosen = st.session_state.setdefault("omni_selected", {})
    if st.session_state.get(f"omni-pick-{rid}"):
        chosen[rid] = True
    else:
        chosen.pop(rid, None)


def _on_add(rid: str) -> None:
    row = _rows_by_id().get(rid)
    if row is None:
        return
    added, problem = add_rows_to_slip([row])
    if problem:
        _notice("warning", problem)
    elif added:
        _notice("success", f"Added {row.selection} to the slip.")


def _clear_selection() -> None:
    chosen = st.session_state.setdefault("omni_selected", {})
    for rid in list(chosen):
        st.session_state[f"omni-pick-{rid}"] = False
    chosen.clear()


def _on_bundle() -> None:
    rows = _selected_rows()
    if len(rows) < 2:
        return
    added, problem = add_rows_to_slip(rows)
    if problem:
        _notice("warning", f"Nothing was added. {problem} Uncheck one of the contradictory props and bundle again.")
        return
    _notice("success", f"Bundled {len(rows)} legs into the Parlay Slip ({added} new).")
    _clear_selection()


def _open_studio() -> None:
    st.session_state["nav_page"] = NAV_STUDIO


# --------------------------------------------------------------------------- data


def _board_is_live(board: PricedBoard) -> bool:
    source = str(board.odds_source or "")
    return bool(source) and not source.startswith("synthetic") and not source.startswith("mixed:synthetic")


def _load_feeds(boards: dict[str, PricedBoard], controls: dict) -> list[SportFeed]:
    cache = st.session_state.setdefault("omni_feeds", {})
    force = bool(st.session_state.pop("omni_refresh", False))
    todo = []
    for sport in sc.SPORTS:
        board = boards.get(sport)
        if board is None:
            continue
        token = (id(board), bool(controls.get("live")), bool(controls.get("odds_key")))
        entry = cache.get(sport)
        if force or entry is None or entry["token"] != token:
            todo.append((sport, board, token))
    if todo:
        st.session_state["omni_auto_steps"] = 0
        with st.spinner("Reading NHL, NBA and NFL lines from every book..."):
            for sport, board, token in todo:
                feed = build_sport_feed(
                    board,
                    sport,
                    sharp_key=str(controls.get("odds_key") or ""),
                    live=bool(controls.get("live")) and _board_is_live(board),
                )
                cache[sport] = {"token": token, "feed": feed}
    return [cache[sport]["feed"] for sport in sc.SPORTS if sport in cache]


def _build_rows(feeds: list[SportFeed]) -> list[sc.ScreenerRow]:
    version = st.session_state.get("omni_scan_version", 0)
    token = (tuple(id(feed) for feed in feeds), version)
    cached = st.session_state.get("omni_rows")
    if cached is not None and cached[0] == token:
        return cached[1]
    quotes = [quote for feed in feeds for quote in feed.quotes]
    model_probs: dict = {}
    for feed in feeds:
        model_probs.update(feed.model_probs)
    rows = sc.build_rows(quotes, model_probs, st.session_state.get("omni_scan", {}))
    st.session_state["omni_rows"] = (token, rows)
    st.session_state["omni_rows_by_id"] = {row.id: row for row in rows}
    return rows


# --------------------------------------------------------------------------- cells


def _esc(value) -> str:
    return html.escape(str(value), quote=True)


def _sport_badge(sport: str) -> str:
    return f"<span class='om-sport om-{sport.lower()}'>{_esc(sport)}</span>"


def _book_name(book: str) -> str:
    return sc.BOOK_NAMES.get(book, str(book).replace("_", " ").title())


def _selection_cell(row: sc.ScreenerRow, with_logo: bool = False) -> str:
    logo = ""
    if with_logo and row.logo_team:
        logo = team_logo_html(row.sport, row.logo_team, size=22, css_class="om-logo")
    if row.is_team:
        main = _esc(row.selection)
        sub = _esc(row.market_label)
    else:
        main = _esc(row.entity)
        detail = row.selection[len(row.entity) :].strip() if row.selection.startswith(row.entity) else row.selection
        milestone = f" ({row.milestone})" if row.milestone else ""
        sub = _esc(f"{detail}{milestone}")
    return f"<div class='om-cell'>{logo}<b>{main}</b><br><span class='om-muted'>{sub}</span></div>"


def _odds_cell(row: sc.ScreenerRow) -> str:
    price = format_american(row.price)
    if row.est:
        return f"<div class='om-cell'><b>{price}</b> <span class='om-chip est'>est.</span><br><span class='om-muted'>estimated, not a book price</span></div>"
    return f"<div class='om-cell'><b>{price}</b> <span class='om-muted'>{_esc(_book_name(row.book))}</span><br><span class='om-muted'>win {max(0.0, row.win_prob) * 100:.0f}% ({_esc(row.win_src)})</span></div>"


def _hit_cell(row: sc.ScreenerRow) -> str:
    rate = row.l10_rate
    if rate is None or row.hit is None:
        why = "no game log feed" if not row.supports_log else "not scanned yet"
        return f"<div class='om-cell'>—<br><span class='om-muted'>{why}</span></div>"
    percent = max(0.0, min(100.0, rate * 100.0))
    return (
        f"<div class='om-cell'><b>{row.hit.l10_hits}/{row.hit.l10_games}</b> [{percent:.0f}%]"
        f"<div class='om-bar'><i style='width:{percent:.0f}%'></i></div></div>"
    )


def _streak_cell(row: sc.ScreenerRow) -> str:
    info = sc.streak_info(row)
    if info is None:
        return _hit_cell(row)
    return f"<div class='om-cell'><span class='om-streak'>{_esc(info[1])}</span></div>"


def _cushion_cell(row: sc.ScreenerRow, streak: bool = False) -> str:
    value = row.cushion_l10
    if streak:
        info = sc.streak_info(row)
        value = info[2] if info is not None else None
    if value is None or value < 0:
        return "<div class='om-cell'>—</div>"
    return f"<div class='om-cell'><b>+{value:.1f}</b> <span class='om-muted'>above line</span></div>"


def _chips(row: sc.ScreenerRow, with_delta: bool = False) -> str:
    chips = []
    if row.est:
        chips.append("<span class='om-chip est'>est. price</span>")
    if row.has_flag("hot_odds"):
        chips.append("<span class='om-chip hot'>🔥 Hot Odds</span>")
    if row.has_flag("floor_100"):
        chips.append("<span class='om-chip floor'>🧊 100% Floor</span>")
    if row.ev_pct is not None and row.ev_pct > 0:
        chips.append(f"<span class='om-chip ev'>+{row.ev_pct:.1f}% EV</span>")
    if with_delta and row.delta_pp is not None and row.delta_pp > 0:
        chips.append(f"<span class='om-chip ev'>Δ +{row.delta_pp:.0f} pts vs price</span>")
    if row.has_flag("soft_book"):
        chips.append(f"<span class='om-chip soft' title='{_esc(row.soft_note)}'>💎 Soft book</span>")
    return "<div class='om-cell'>" + ("".join(chips) if chips else "<span class='om-muted'>—</span>") + "</div>"


def _row_html(row: sc.ScreenerRow, deck: str) -> str:
    streak = deck == DECK_STREAKS
    parts = [
        f"<div class='om-cell'>{_sport_badge(row.sport)}</div>",
        f"<div class='om-cell'>{_esc(row.matchup_label)}</div>",
        _selection_cell(row, with_logo=streak),
        _odds_cell(row),
        _streak_cell(row) if streak else _hit_cell(row),
        _cushion_cell(row, streak),
        _chips(row, with_delta=deck == DECK_HOT),
    ]
    return "<div class='om-row om-grid'>" + "".join(parts) + "</div>"


def _header_html(deck: str) -> str:
    third = "Streak" if deck == DECK_STREAKS else "L10 Hit Rate"
    heads = ["Sport", "Matchup", "Player / Team Selection", "Best Odds", third, "Cushion", "Edge Badges"]
    return "<div class='om-head om-grid'>" + "".join(f"<div>{_esc(h)}</div>" for h in heads) + "</div>"


# --------------------------------------------------------------------------- grids


def _render_grid(rows: list[sc.ScreenerRow], deck: str) -> None:
    head_a, head_b, head_c = st.columns([0.8, 12, 1.7], vertical_alignment="center")
    head_a.markdown("<div class='om-head om-pick-head'>Pick</div>", unsafe_allow_html=True)
    head_b.markdown(_header_html(deck), unsafe_allow_html=True)
    head_c.markdown("<div class='om-head'>Action</div>", unsafe_allow_html=True)
    slip_ids = {item.get("id") for item in _slip()}
    selected = st.session_state.setdefault("omni_selected", {})
    for row in rows:
        left, middle, right = st.columns([0.8, 12, 1.7], vertical_alignment="center")
        key = f"omni-pick-{row.id}"
        if key not in st.session_state:
            st.session_state[key] = row.id in selected
        if row.selectable:
            left.checkbox("Pick", key=key, on_change=_on_pick, args=(row.id,), label_visibility="collapsed")
        else:
            left.markdown("<span class='om-muted'>est.</span>", unsafe_allow_html=True)
        middle.markdown(_row_html(row, deck), unsafe_allow_html=True)
        if not row.selectable:
            right.button("No price", key=f"omni-add-{row.id}", disabled=True, help="Estimated prices cannot be added.")
        elif f"omni|{row.id}" in slip_ids:
            right.button("✓ In Slip", key=f"omni-add-{row.id}", disabled=True)
        else:
            right.button("+ Add to Slip", key=f"omni-add-{row.id}", on_click=_on_add, args=(row.id,))


def _shop_price_cell(row: sc.ScreenerRow | None, book: str, best_book: str) -> str:
    if row is None or book not in row.books:
        if book in sc.BOOK_NAMES and book in {"bet365", "pinnacle"}:
            return f"<div class='om-cell'><span class='om-na'>{ODDS_NOTE}</span></div>"
        return "<div class='om-cell'><span class='om-muted'>—</span></div>"
    cls = "om-best" if book == best_book else ""
    line = "" if row.line is None or row.side == "YES" else f"<br><span class='om-muted'>{'o' if row.side == 'OVER' else 'u'}{row.line:g}</span>"
    return f"<div class='om-cell'><span class='{cls}'>{format_american(row.books[book])}</span>{line}</div>"


def _shop_html(shop: sc.ShopRow) -> str:
    pick = shop.pick
    cells = [f"<div class='om-cell'>{_sport_badge(pick.sport)}</div>", f"<div class='om-cell'>{_esc(shop.matchup_label)}</div>"]
    label = pick.selection
    cells.append(f"<div class='om-cell'><b>{_esc(shop.entity)}</b><br><span class='om-muted'>{_esc(label[len(shop.entity):].strip() if label.startswith(shop.entity) else pick.market_label)}</span></div>")
    for book in ("draftkings", "fanduel", "bet365", "pinnacle"):
        # one cell per book: the row at the line this book posted (several lines can exist)
        match = next((row for row in shop.rows if book in row.books), None)
        cells.append(_shop_price_cell(match, book, shop.soft_book))
    cells.append(f"<div class='om-cell'><b>{max(0.0, pick.fair_prob) * 100:.0f}%</b><br><span class='om-muted'>no-vig</span></div>")
    flag = f"<span class='om-chip soft'>💎 {_esc(shop.reason)}</span>" if shop.flagged else "<span class='om-muted'>no 25-cent / 1-unit edge</span>"
    cells.append(f"<div class='om-cell'>{flag}</div>")
    return "<div class='om-row om-grid-shop'>" + "".join(cells) + "</div>"


def _render_shop(shops: list[sc.ShopRow]) -> None:
    heads = ["Sport", "Matchup", "Selection", "DraftKings", "FanDuel", "Bet365", "Pinnacle", "Consensus", "Value flag"]
    head_a, head_b, head_c = st.columns([0.8, 12, 1.7], vertical_alignment="center")
    head_a.markdown("<div class='om-head om-pick-head'>Pick</div>", unsafe_allow_html=True)
    head_b.markdown("<div class='om-head om-grid-shop'>" + "".join(f"<div>{h}</div>" for h in heads) + "</div>", unsafe_allow_html=True)
    head_c.markdown("<div class='om-head'>Action</div>", unsafe_allow_html=True)
    slip_ids = {item.get("id") for item in _slip()}
    selected = st.session_state.setdefault("omni_selected", {})
    for shop in shops:
        row = shop.pick
        left, middle, right = st.columns([0.8, 12, 1.7], vertical_alignment="center")
        key = f"omni-pick-{row.id}"
        if key not in st.session_state:
            st.session_state[key] = row.id in selected
        left.checkbox("Pick", key=key, on_change=_on_pick, args=(row.id,), label_visibility="collapsed")
        middle.markdown(_shop_html(shop), unsafe_allow_html=True)
        if f"omni|{row.id}" in slip_ids:
            right.button("✓ In Slip", key=f"omni-add-{row.id}", disabled=True)
        else:
            right.button("+ Add to Slip", key=f"omni-add-{row.id}", on_click=_on_add, args=(row.id,))


# --------------------------------------------------------------------------- scanning


def _scan_state() -> tuple[dict, dict]:
    return st.session_state.setdefault("omni_scan", {}), st.session_state.setdefault("omni_attempts", {})


def _scan_step(rows: list[sc.ScreenerRow]) -> str:
    """Scan up to one budget of players, saving results in session state. Returns the status."""
    series, attempts = _scan_state()
    targets = sc.scan_targets(rows, series, attempts, limit=scan.DEFAULT_BUDGET)
    if not targets:
        return "empty"
    bar = st.progress(0.0, text=f"Scanning {len(targets)} player game logs...")

    def tick(done: int, total: int) -> None:
        bar.progress(min(1.0, done / max(total, 1)), text=f"Scanning game logs {done} of {total}...")

    results, errors, status = scan.scan_batch(targets, budget=scan.DEFAULT_BUDGET, progress=tick)
    bar.empty()
    for key, value in results.items():
        series[key] = value
    for key, count in errors.items():
        attempts[key] = attempts.get(key, 0) + count
    st.session_state["omni_scan_version"] = st.session_state.get("omni_scan_version", 0) + 1
    st.session_state["omni_last_scan"] = f"{len(results)} players read, {len(errors)} failed" + (" (rate limited, will retry)" if status == "limited" else "")
    return status


def _on_scan_press() -> None:
    st.session_state["omni_scan_now"] = True
    st.session_state["omni_auto_steps"] = 0


def _on_refresh() -> None:
    st.session_state["omni_refresh"] = True
    st.session_state["omni_rows"] = None


# --------------------------------------------------------------------------- page


def _filters(counts: dict[str, int]) -> dict:
    with st.container(key="omni-filters"):
        top = st.columns([3.2, 2.2, 2.6])
        with top[0]:
            st.segmented_control(
                "Sport",
                ["ALL", *sc.SPORTS],
                default="ALL",
                key="omni_sport",
                format_func=lambda value: f"{value} ({counts.get(value, 0)})",
                label_visibility="collapsed",
            )
        with top[1]:
            st.selectbox("Market", sc.CATEGORIES, key="omni_category", label_visibility="collapsed")
        with top[2]:
            st.text_input("Search", key="omni_search", placeholder="Search player or team", label_visibility="collapsed")
        bottom = st.columns([2.3, 3.4, 1.4, 1.1])
        with bottom[0]:
            st.slider("Min hit rate (L10)", 50, 100, 50, step=5, format="%d%%", key="omni_hit")
        with bottom[1]:
            st.select_slider(
                "Odds range (min to max)",
                options=ODDS_STEPS,
                value=(ODDS_STEPS[0], ODDS_STEPS[-1]),
                key="omni_odds",
                format_func=lambda value: format_american(value),
            )
        with bottom[2]:
            st.selectbox("Rows per page", PAGE_SIZES, key="omni_page_size")
        with bottom[3]:
            st.caption(" ")
            st.button("↻ Refresh feed", key="omni_refresh_btn", on_click=_on_refresh, help="Re-read the books (uses the 15-minute cache when it is still fresh).")
    low, high = st.session_state.get("omni_odds", (ODDS_STEPS[0], ODDS_STEPS[-1]))
    return {
        "sport": st.session_state.get("omni_sport") or "ALL",
        "category": st.session_state.get("omni_category") or "All Markets",
        "search": st.session_state.get("omni_search") or "",
        "min_hit": float(st.session_state.get("omni_hit", 50)) / 100.0,
        "odds_low": None if low == ODDS_STEPS[0] else low,
        "odds_high": None if high == ODDS_STEPS[-1] else high,
    }


def _slip_summary() -> None:
    slip = [leg for leg in _slip() if leg.get("decimal")]
    left, right = st.columns([5, 1.4], vertical_alignment="center")
    if not slip:
        left.caption("Parlay Slip: empty. Check props or press + Add to Slip. The slip is shared with the Studio page.")
    else:
        decimal = combine_decimal([float(leg["decimal"]) for leg in slip])
        left.markdown(f"**Parlay Slip:** {len(slip)} leg{'s' if len(slip) != 1 else ''} · Combined odds **{format_american(decimal_to_american(decimal))}** · review stake and save in the ⚡ Studio.")
    right.button("Open Studio", key="omni_open_studio", on_click=_open_studio)


def _drawer(selected: list[sc.ScreenerRow]) -> None:
    st.markdown(DRAWER_PAD, unsafe_allow_html=True)
    american, decimal = sc.combined_odds(selected)
    with st.container(key="omni-drawer"):
        text, clear, bundle = st.columns([5, 1.3, 2.4], vertical_alignment="center")
        text.markdown(f"**{len(selected)} Legs Selected** | Combined Odds: **{format_american(american)}** ({decimal:.2f}x)")
        clear.button("Clear", key="omni_clear", on_click=_clear_selection)
        bundle.button("Bundle into Parlay Slip", key="omni_bundle", type="primary", on_click=_on_bundle)


def _paginate(total: int) -> tuple[int, int, int]:
    size = int(st.session_state.get("omni_page_size", PAGE_SIZES[0]) or PAGE_SIZES[0])
    pages = max(1, (total + size - 1) // size)
    signature = (
        st.session_state.get("omni_sport"),
        st.session_state.get("omni_category"),
        st.session_state.get("omni_search"),
        st.session_state.get("omni_hit"),
        st.session_state.get("omni_odds"),
        st.session_state.get("omni_deck"),
        size,
    )
    if st.session_state.get("omni_page_sig") != signature:
        st.session_state["omni_page_sig"] = signature
        st.session_state["omni_page"] = 1
    current = min(max(int(st.session_state.get("omni_page", 1) or 1), 1), pages)
    st.session_state["omni_page"] = current
    if pages > 1:
        st.number_input(f"Page (of {pages})", min_value=1, max_value=pages, step=1, key="omni_page")
        current = int(st.session_state["omni_page"])
    return (current - 1) * size, size, pages


def render_omniscreener(boards: dict[str, PricedBoard], controls: dict) -> None:
    """The whole OmniScreener page. `boards` are the Studio boards already loaded per league."""
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("<div class='om-title'>OMNI<span>SCREENER</span> · Master Feed</div>", unsafe_allow_html=True)
    st.caption("NHL, NBA and NFL lines from every real book on your plan, with hit rates from game logs. Prices and hit rates are labelled with their source on every row.")

    for kind, text in st.session_state.pop("omni_notices", []):
        (st.success if kind == "success" else st.warning)(text)

    feeds = _load_feeds(boards, controls)
    rows = _build_rows(feeds)
    counts = {"ALL": len(rows), **{sport: sum(1 for row in rows if row.sport == sport) for sport in sc.SPORTS}}

    flt = _filters(counts)
    with st.container(key="omni-decks"):
        st.segmented_control("Deck", DECKS, default=DECK_ANVIL, key="omni_deck", format_func=lambda value: DECK_ICONS[value], label_visibility="collapsed")
    deck = st.session_state.get("omni_deck") or DECK_ANVIL
    st.caption(DECK_BLURB[deck])
    _slip_summary()

    # ---- scan panel
    series, attempts = _scan_state()
    scanned, scannable, no_log = sc.scan_counts(rows)
    with st.container(key="omni-scan"):
        info, press, auto = st.columns([5, 1.6, 1.8], vertical_alignment="center")
        info.markdown(f"**Scanned {scanned} of {scannable} props** with a free game log · {no_log} more props (blocks, hits, power-play points) have no free game-log feed and show —")
        pending = sc.scan_targets(rows, series, attempts, limit=10_000)
        press.button(f"Scan {min(scan.DEFAULT_BUDGET, len(pending))} more" if pending else "All scanned", key="omni_scan_btn", on_click=_on_scan_press, disabled=not pending)
        auto.toggle("Auto-continue", value=True, key="omni_auto", help="Keeps scanning 25 players at a time until every prop is read or a rate limit stops it.")
        st.progress(0.0 if not scannable else min(1.0, scanned / scannable), text=f"{scanned}/{scannable} props have a hit rate. Source: ESPN game logs, current regular season, last 10 games played. Cached 3 hours.")
        last = st.session_state.get("omni_last_scan")
        if last:
            st.caption(f"Last scan: {last}.")
        left_s = scan.espn_cooldown_seconds()
        if left_s:
            st.caption(f"NHL API cooling down ({left_s}s); Studio uses saved data. OmniScreener reads ESPN game logs, so scanning is not affected.")
    for feed in feeds:
        if feed.note:
            st.caption(feed.note)
        if feed.games == 0:
            st.caption(f"{feed.sport}: no games today.")

    # ---- deck body
    st.session_state.setdefault("omni_selected", {})
    if deck == DECK_SHOP:
        shops = [shop for shop in sc.deck_line_shop(rows)]
        shops = [
            shop
            for shop in shops
            if sc.filter_rows([shop.pick], sport=flt["sport"], category=flt["category"], search=flt["search"], min_hit=0.5, odds_low=None, odds_high=None)
        ]
        total = len(shops)
        start, size, _pages = _paginate(total)
        st.caption(
            f"{total} props priced by 2+ real books. Sharp consensus = the no-vig average of the real books that posted both sides (only DraftKings and FanDuel are on your plan, so it is a 2-book consensus). "
            "A price counts as value when it beats another book or the consensus by 25+ cents (cents = American price gap with the -100..+100 dead zone removed, so -105 vs +105 is 10), or when a book's line is 1+ full stat unit friendlier. Bet365 and Pinnacle show n/a because no real price came back."
        )
        with st.container(key="omni-shop"):
            if not shops:
                st.info("No prop is priced by two real books right now. Press Refresh feed after the slate loads.")
            _render_shop(shops[start : start + size])
        visible_count = total
    else:
        if deck == DECK_ANVIL:
            base = sc.deck_anvil(rows)
        elif deck == DECK_HOT:
            base = sc.deck_hot_odds(rows)
        elif deck == DECK_STREAKS:
            base = sc.deck_streaks(rows)
        else:
            base = sorted(rows, key=lambda row: (row.est, not row.scanned, -row.win_prob, row.id))
        shown = sc.filter_rows(base, **flt)
        total = len(shown)
        start, size, _pages = _paginate(total)
        if deck == DECK_FEED:
            st.caption("Hit-rate cells show — until a prop's player has been scanned. The Hit Rate slider above 50% hides unscanned rows.")
        else:
            st.caption("This deck lists scanned rows only. EV% uses the win probability at the best real price (not the hit rate); negative EV shows no badge. Delta = hit rate minus the price's implied probability.")
        with st.container(key="omni-grid"):
            if not shown:
                if scanned == 0 and deck != DECK_FEED:
                    st.info("No scanned props yet. Game logs load in batches of 25 (press Scan more, or leave Auto-continue on).")
                else:
                    st.info("Nothing matches these filters in this deck right now.")
            _render_grid(shown[start : start + size], deck)
        visible_count = total
    st.caption(f"{visible_count} rows in {deck}. Sources: SharpAPI (DraftKings, FanDuel) · Studio model blend where available, else no-vig market · ESPN game logs.")

    # ---- floating drawer
    selected = _selected_rows()
    if len(selected) >= 2:
        _drawer(selected)
    elif len(selected) == 1:
        st.caption("1 prop checked. Check one more to bundle, or press + Add to Slip.")

    # ---- progressive scan (after the page is painted, one step per run)
    if st.session_state.pop("omni_scan_now", False):
        _scan_step(rows)
        st.rerun()
    elif st.session_state.get("omni_auto", True) and pending and st.session_state.get("omni_auto_steps", 0) < MAX_AUTO_STEPS:
        st.session_state["omni_auto_steps"] = st.session_state.get("omni_auto_steps", 0) + 1
        status = _scan_step(rows)
        if status != "limited":
            st.rerun()
