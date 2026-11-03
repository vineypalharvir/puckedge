"""The 100% Hit Rates matchup card. Used in the scoreboard dialog and in the Find tab.

Renders from a cached `GameData` scan (fetched only when the user opens the card or presses Scan),
so switching modes never refetches. Prices come from real board legs; a prop with no posted price
still lists and says "no line posted".
"""

from __future__ import annotations

import html
from typing import Callable

import streamlit as st

from config import BOOK_LABELS
from src.api.data_models import PricedBoard
from src.engine.devig import format_american
from src.engine.hit_rates import (
    HIGH_CUSHION,
    SGP_HIGH_AMERICAN,
    SGP_LOW_AMERICAN,
    GameData,
    HitRateProp,
    SgpCombo,
    SgpResult,
    build_sgp,
    display_rows,
    evaluate_game,
    slip_quote,
    specs_for,
)
from src.utils.team_assets import team_logo_html

SUBTITLE = "These props have cashed in every game this season (or L10)."
MISSED_SUBTITLE = "These props cleared in 9 of the last 10 games and missed once by exactly 1."
MODES: dict[str, tuple[str, bool]] = {
    "Season 100%": ("season", False),
    "Last 10 Games (100%)": ("L10", False),
    "Last 5 Games (100%)": ("L5", False),
    "90% Missed by 1": ("L10", True),
}
DEFAULT_MODE = "Season 100%"
MAX_ROWS = 60
STARTED_STATES = {"LIVE", "CRIT", "IN", "FINAL", "OFF", "OFFICIAL"}

HIT_RATES_CSS = """
.hr-head { display: flex; align-items: center; flex-wrap: wrap; gap: 0.6rem; margin: 0.2rem 0 0.1rem; }
.hr-head h3 { margin: 0; color: #FFFFFF; font-size: 1.25rem; letter-spacing: 0.04em; font-weight: 800; }
.hr-head h3 span { color: #9333EA; }
.hr-head .hr-logo { width: 30px; height: 30px; }
.hr-sub { color: #9CA3AF; font-size: 0.85rem; margin: 0 0 0.5rem; }
.hr-group { color: #D8B4FE; font-size: 0.78rem; font-weight: 750; letter-spacing: 0.08em; text-transform: uppercase; margin: 0.7rem 0 0.25rem; }
.hr-row { display: flex; align-items: center; flex-wrap: wrap; gap: 0.35rem 0.7rem; background: #121216; border: 1px solid #1F1F28; border-radius: 10px; padding: 0.45rem 0.7rem; min-height: 2.9rem; box-sizing: border-box; }
.hr-row.trap { border-color: #5B3A0A; }
.hr-row .hr-logo { width: 26px; height: 26px; flex: 0 0 26px; }
.hr-who { display: flex; flex-direction: column; min-width: 9.5rem; line-height: 1.15; flex: 1 1 11rem; }
.hr-who b { color: #FFFFFF; font-size: 0.92rem; overflow-wrap: anywhere; }
.hr-who small { color: #9CA3AF; font-size: 0.7rem; overflow-wrap: anywhere; }
.hr-prop { color: #F3F4F6; font-weight: 700; font-size: 0.92rem; min-width: 6.5rem; }
.hr-streak { background: rgba(147, 51, 234, 0.18); border: 1px solid rgba(147, 51, 234, 0.55); color: #E9D5FF; border-radius: 999px; padding: 0.08rem 0.6rem; font-weight: 800; font-size: 0.8rem; font-variant-numeric: tabular-nums; }
.hr-streak.miss { background: rgba(251, 191, 36, 0.12); border-color: rgba(251, 191, 36, 0.5); color: #FDE68A; }
.hr-cushion { color: #D1D5DB; font-size: 0.8rem; font-variant-numeric: tabular-nums; }
.hr-cushion.none { color: #6B7280; }
.hr-badge { background: #052E16; color: #86EFAC; border-radius: 999px; padding: 0.08rem 0.5rem; font-size: 0.7rem; font-weight: 700; }
.hr-trap { background: #422006; color: #FDE68A; border-radius: 999px; padding: 0.08rem 0.55rem; font-size: 0.72rem; font-weight: 700; cursor: help; }
.hr-warn { background: #3B0F0F; color: #FCA5A5; border-radius: 999px; padding: 0.08rem 0.55rem; font-size: 0.7rem; font-weight: 700; cursor: help; }
.hr-price { margin-left: auto; font-variant-numeric: tabular-nums; font-size: 0.82rem; color: #FFFFFF; background: #0A0A0C; border: 1px solid #1F1F28; border-radius: 8px; padding: 0.12rem 0.5rem; white-space: nowrap; }
.hr-price b { color: #A78BFA; }
.hr-price.alt { border-color: #5B3A0A; }
.hr-price.none { color: #6B7280; }
.hr-note { color: #9CA3AF; font-size: 0.75rem; margin: 0.15rem 0; }
.hr-sgp { background: #0A0A0C; border: 1px solid #1F1F28; border-radius: 12px; padding: 0.6rem 0.8rem; margin: 0.4rem 0 0.6rem; }
.hr-sgp .hr-leg { color: #E5E7EB; font-size: 0.85rem; margin: 0.15rem 0; }
.hr-sgp .hr-total { color: #FFFFFF; font-weight: 800; }
"""


def esc(value: object) -> str:
    """HTML-escape and keep `$` from rendering as math in st.markdown."""
    return html.escape(str(value), quote=True).replace("$", "&#36;")


def book_name(key: str) -> str:
    return BOOK_LABELS.get(key, key.replace("_", " ").replace("-", " ").title())


def price_html(prop: HitRateProp) -> str:
    price = prop.price
    if price is None:
        tip = "The board has no posted sportsbook price for this player and market, so none is shown."
        return f"<span class='hr-price none' title=\"{esc(tip)}\">no line posted</span>"
    leg = price.leg
    posted = f"{book_name(price.book)}: Over {price.line:g} {prop.stat_label} at {format_american(price.american)}"
    if price.exact:
        return f"<span class='hr-price' title=\"{esc(posted)}\">{esc(book_name(price.book))} <b>{esc(format_american(price.american))}</b></span>"
    verified = "The player also cleared that higher line in every game of this window." if price.verified else "The player did NOT clear that higher line in every game, so it is shown for reference only."
    tip = f"{price.note}. {posted}. {verified}"
    return (
        f"<span class='hr-price alt' title=\"{esc(tip)}\">{esc(book_name(price.book))} <b>{esc(format_american(price.american))}</b> "
        f"(Over {price.line:g})</span>"
    )


def row_html(prop: HitRateProp, league: str) -> str:
    """One Cody Brown style prop row: logo, player, floor, streak pill, cushion, badges, price."""
    logo = team_logo_html(league, prop.team, size=26, css_class="hr-logo")
    where = f"{prop.team} vs {prop.opponent}" if prop.opponent else prop.team
    sub = esc(where) + (f" · {esc(prop.note)}" if prop.note else "")
    window = f"{prop.hits} of {prop.games} games"
    streak_tip = f"{window}. Season {prop.season_hits}/{prop.season_games}, last 5 {prop.l5_hits}/{prop.l5_games}. Average {prop.average:.1f}, low {prop.minimum:g}, last game {prop.last:g}."
    if prop.missed_by_one:
        streak = f"<span class='hr-streak miss' title=\"{esc(streak_tip)} The one miss was {prop.miss_value:g}, exactly 1 short.\">{esc(prop.streak)} · missed by 1</span>"
    else:
        streak = f"<span class='hr-streak' title=\"{esc(streak_tip)}\">{esc(prop.streak)}</span>"
    if prop.cushion > 0:
        cushion = f"<span class='hr-cushion' title=\"Average {prop.average:.1f} minus the {prop.line} floor, over the same {prop.games} games.\">+{prop.cushion:.1f} cushion</span>"
    else:
        cushion = "<span class='hr-cushion none' title=\"The average sits on the floor, so there is no margin.\">no cushion</span>"
    badge = f"<span class='hr-badge' title=\"Average is at least {HIGH_CUSHION:g} above the floor.\">High Cushion</span>" if prop.high_cushion else ""
    trap = ""
    if prop.traps:
        reasons = " | ".join(prop.traps)
        trap = f"<span class='hr-trap' title=\"{esc(reasons)}\">&#9888; Trap Matchup</span>"
    warns = "".join(f"<span class='hr-warn' title=\"{esc(text)}\">&#9888; {esc(text.split(' (')[0])}</span>" for text in prop.warnings)
    return (
        f"<div class='hr-row{' trap' if prop.traps else ''}'>{logo}"
        f"<div class='hr-who'><b>{esc(prop.player)}</b><small>{sub}</small></div>"
        f"<div class='hr-prop'>{esc(prop.label)}</div>{streak}{cushion}{badge}{trap}{warns}{price_html(prop)}</div>"
    )


def header_html(league: str, away: str, home: str, missed: bool) -> str:
    logos = (team_logo_html(league, away, size=30, css_class="hr-logo"), team_logo_html(league, home, size=30, css_class="hr-logo"))
    return (
        f"<div class='hr-head'>{logos[0]}<h3>{esc(away)} @ {esc(home)} <span>100% HIT RATES</span></h3>{logos[1]}</div>"
        f"<div class='hr-sub'>{esc(SUBTITLE)}{' ' + esc(MISSED_SUBTITLE) if missed else ''}</div>"
    )


def _started(game) -> bool:
    return str(getattr(game, "game_state", "") or "").upper() in STARTED_STATES


def _in_slip(quote: dict) -> bool:
    return any(item.get("id") == quote["id"] for item in st.session_state.get("slip", []))


def _add_quotes(props: list[HitRateProp], add_quote: Callable[[dict], None]) -> int:
    added = 0
    for prop in props:
        quote = slip_quote(prop)
        if quote is None or _in_slip(quote):
            continue
        add_quote(quote)
        if _in_slip(quote):
            added += 1
    return added


def _combo_text(combo: SgpCombo) -> str:
    return f"{format_american(combo.american)} ({combo.decimal:.2f}x)"


def _render_combo(combo: SgpCombo) -> None:
    lines = "".join(
        f"<div class='hr-leg'>{esc(prop.player)} {esc(prop.label)} · {esc(prop.streak)} · {esc(format_american(prop.price.american))} {esc(book_name(prop.price.book))}"
        f"{' · &#9888; trap' if prop.is_trap else ''}</div>"
        for prop in combo.props
    )
    st.markdown(f"<div class='hr-sgp'>{lines}<div class='hr-total'>Combined {esc(_combo_text(combo))}</div></div>", unsafe_allow_html=True)


def _render_sgp_result(result: SgpResult, ctx: str, add_quote: Callable[[dict], None], added: bool) -> None:
    if result.best is not None:
        _render_combo(result.best)
        st.caption(
            "Added to your slip." if added else result.message
        )
        st.caption(
            "Same-game legs are multiplied as if they were independent prices. A sportsbook may shorten the combo or not offer it as a parlay."
        )
        return
    st.warning(result.message)
    for index, combo in enumerate(result.closest):
        _render_combo(combo)
        if st.button(f"Add these {len(combo.props)} anyway ({format_american(combo.american)})", key=f"hr-sgp-any-{ctx}-{index}"):
            count = _add_quotes(list(combo.props), add_quote)
            st.session_state["hr_sgp_flash"] = f"Added {count} leg{'s' if count != 1 else ''} to your slip."
            st.rerun()


def _scan(board: PricedBoard, game, league: str) -> GameData:
    from src.stats.milestones import scan_game

    legs = [*board.legs, *board.prop_legs]
    bar = st.progress(0.0, text="Starting the scan")

    def progress(fraction: float, text: str) -> None:
        bar.progress(fraction, text=text)

    try:
        return scan_game(league, game, legs, progress=progress)
    finally:
        bar.empty()


def render_hit_rates(
    board: PricedBoard,
    game,
    *,
    league: str,
    preferred_book: str,
    add_quote: Callable[[dict], None],
    ctx: str,
    autoscan: bool,
) -> None:
    """Header, mode toggle, prop list and the undefeated-SGP builder for one game."""
    key = f"{league}|{game.game_id}"
    scans: dict = st.session_state.setdefault("hr_scans", {})
    data: GameData | None = scans.get(key)
    mode_key = f"hr-mode-{ctx}"
    current = st.session_state.get(mode_key) or DEFAULT_MODE
    scope, missed = MODES.get(current, MODES[DEFAULT_MODE])
    st.markdown(header_html(league, game.away.abbrev, game.home.abbrev, missed), unsafe_allow_html=True)
    if data is None:
        go_key = f"hr-go-{ctx}-{key}"
        if not autoscan and not st.session_state.get(go_key):
            st.caption("Game logs are fetched only when you ask, to stay under the NHL and SharpAPI rate limits.")
            if st.button("Scan 100% hit rates for this game", key=f"hr-scan-{ctx}", type="primary"):
                st.session_state[go_key] = True
                st.rerun()
            return
        with st.spinner("Scanning game logs for this matchup..."):
            data = _scan(board, game, league)
        scans[key] = data
        st.session_state.pop(go_key, None)
    top, again = st.columns([6, 1], vertical_alignment="center")
    with top:
        picked = st.segmented_control("Window", list(MODES), selection_mode="single", default=DEFAULT_MODE, key=mode_key, label_visibility="collapsed")
    if again.button("Rescan", key=f"hr-rescan-{ctx}", help="Drop this scan and fetch again. Saved game logs are reused unless a request failed."):
        scans.pop(key, None)
        st.rerun()
    scope, missed = MODES.get(picked or DEFAULT_MODE, MODES[DEFAULT_MODE])
    if _started(game):
        st.caption("This game has started or finished, so its own result is already in these streaks.")
    legs = [*board.legs, *board.prop_legs]
    rows = evaluate_game(data, legs, scope=scope, missed=missed, preferred=preferred_book)
    shown = display_rows(rows)
    flash = st.session_state.pop("hr_sgp_flash", "")
    if flash:
        st.success(flash)
    for note in data.notes:
        st.warning(note) if data.partial else st.caption(note)
    if not shown:
        window = {"season": "this season", "L10": "the last 10 games", "L5": "the last 5 games"}[scope]
        extra = " A window of 10 needs players with 10 games, which is rare this early in a season." if scope == "L10" and not missed else ""
        st.info(f"No player has a floor that cashed in every game over {window}." if not missed else "No player cleared a floor in exactly 9 of the last 10 with one miss of exactly 1.")
        if extra:
            st.caption(extra.strip())
    else:
        priced = sum(1 for prop in shown if prop.price is not None)
        st.caption(f"{len(shown)} props across {len({prop.player for prop in shown})} players · {priced} with a real posted price · {data.loaded} of {data.total} data sets or players loaded")
    group_order = {spec.key: index for index, spec in enumerate(specs_for(league))}
    ordered = sorted(shown[:MAX_ROWS], key=lambda prop: group_order.get(prop.stat, 99))
    last_group = None
    for index, prop in enumerate(ordered):
        if prop.stat != last_group:
            st.markdown(f"<div class='hr-group'>{esc(prop.stat_label)}</div>", unsafe_allow_html=True)
            last_group = prop.stat
        left, right = st.columns([11, 2], vertical_alignment="center")
        left.markdown(row_html(prop, league), unsafe_allow_html=True)
        quote = slip_quote(prop)
        if quote is None:
            reason = "No posted price for this prop, so it cannot go on the slip." if prop.price is None else f"{prop.price.note}. It was not added because the player did not clear that line every game."
            right.button("+ Add to Slip", key=f"hr-add-{ctx}-{index}", disabled=True, help=reason, width="stretch")
        elif _in_slip(quote):
            right.button("On slip", key=f"hr-add-{ctx}-{index}", disabled=True, width="stretch")
        elif right.button("+ Add to Slip", key=f"hr-add-{ctx}-{index}", width="stretch"):
            add_quote(quote)
            st.rerun()
    if len(shown) > MAX_ROWS:
        st.caption(f"Showing the top {MAX_ROWS} of {len(shown)} props.")
    for text in data.skipped:
        st.caption(f"Not checked: {text}.")
    if data.excluded:
        st.caption("Left out because they are listed out: " + ", ".join(data.excluded) + ".")
    if data.spread is not None:
        st.caption(f"Spread used for the blowout check: {data.spread:g} ({data.spread_source or 'ESPN'}) unless the board has a real one.")
    st.markdown("**One-click SGP**")
    st.caption(
        f"Picks the 3 safest 100% props from this game that have a real posted price, prefers non-trap props, and aims for "
        f"{format_american(SGP_LOW_AMERICAN)} to {format_american(SGP_HIGH_AMERICAN)}. It never pads a price to fit."
    )
    result_key = f"hr-sgp-result-{ctx}"
    if st.button("Build Undefeated SGP", key=f"hr-sgp-{ctx}", type="primary"):
        sgp_scope = "season" if missed else scope
        pool = evaluate_game(data, legs, scope=sgp_scope, missed=False, preferred=preferred_book)
        result = build_sgp(pool)
        added = False
        if result.best is not None:
            count = _add_quotes(list(result.best.props), add_quote)
            added = count > 0
            if count < len(result.best.props):
                st.session_state.setdefault("slip_notices", []).append("Some SGP legs were already on the slip or conflicted, so fewer were added.")
        st.session_state[result_key] = (key, result, added)
        if added:
            st.rerun()
    saved = st.session_state.get(result_key)
    if saved and saved[0] == key:
        _render_sgp_result(saved[1], ctx, add_quote, saved[2])
