"""DarkNode Parlay Engine dashboard.

Run from the project root:

    .venv\\Scripts\\python.exe -m streamlit run app.py
"""

from __future__ import annotations

import html
import os
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.api.data_models import BuiltParlay, MarketLeg, PricedBoard
from config import BOOK_LABELS, ROOT
from src.engine.boost import grade_boost, is_context_line, is_player_line, manual_leg, match_slip_line, parse_boost_price, split_bet_slip
from src.stats.lookup import describe_player_leg, use_book_price
from src.engine.correlation import slip_conflict, synergy, ticket_conflicts
from src.engine.devig import combine_decimal, decimal_to_american, expected_value, format_american, implied_to_american
from src.stats.books import matching_books
from src.stats.opponent import adjusted_probability, tonight
from src.stats.catalog import markets_for
from src.stats.rates import parse_custom_line, rate_row, rows_for_market, same_game_note, series_from_gamelog, series_from_nhl, series_from_overview, slip_chance
from src.stats.search import espn_profile, load_nhl_landing, load_player, nhl_profile, search_entities
from src.utils.kelly import QUARTER_KELLY, exposure_message, exposure_pct, exposure_text, kelly_fraction, sizing_warning
from src.utils.money import format_money, payout_amount, profit_amount
from src.utils.team_assets import team_logo_html, team_logo_url
from src.ui.hit_rates_view import HIT_RATES_CSS, render_hit_rates
from src.engine.generator import PROFILES, generate_parlays
from src.engine.sgp import ParlayFilter, generate_valid_parlays
from src.engine.target_hunt import hunt_parlays
from src.access import OWNER_USERNAME, UNPAID_MESSAGE, access_allowed, load_local_access, save_access
from src.usage_log import (
    authenticate,
    clear_remembered,
    create_account,
    list_accounts,
    load_remembered,
    lookup_public_ip,
    password_matches,
    read_events,
    record_event,
    remember_ips,
    save_remembered,
)
from src.limits import capped_stake, load_max_stake, save_max_stake
from src.services.slate import load_board
from src.tracker.database import open_session
from src.tracker.repository import get_balance, list_parlays, performance, save_parlay, set_balance
from src.tracker.scheduler import run_settlement_once, start_scheduler
from src.updater import peek_update
from src.ui.omniscreener import NAV_CSS, NAV_OMNI, NAV_OPTIONS, NAV_SETUP, NAV_VAULT, render_omniscreener

st.set_page_config(
    page_title="DarkNode Parlay Engine",
    page_icon="🏒",
    layout="wide",
    initial_sidebar_state="collapsed",
)


def inject_css() -> None:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"], [data-testid="collapsedControl"] { display: none; }
        .stApp, [data-testid="stAppViewContainer"], [data-testid="stHeader"] { background: #0A0A0C; }
        .main .block-container { max-width: 100%; padding-top: 0.6rem; padding-left: 1rem; padding-right: 1rem; }
        h1.brand { font-size: 1.35rem; letter-spacing: 0.12em; margin: 0; font-weight: 750; color: #FFFFFF; }
        h1.brand span { color: #9333EA; }
        .credit { color: #9CA3AF; font-size: 0.8rem; margin: 0.6rem 0 0 0; }
        .together { color: #9333EA; font-size: 1.8rem; font-weight: 750; line-height: 1; }
        .together-label, .fine { color: #9CA3AF; font-size: 0.78rem; }
        .pill { display: inline-block; padding: 0.12rem 0.55rem; border-radius: 999px; font-size: 0.78rem; font-weight: 650; margin-right: 0.35rem; background: #1F1F28; color: #FFFFFF; }
        .pill.warn { background: #422006; color: #FDE68A; }
        .pill.good { background: #052E16; color: #86EFAC; }
        .pill.bad { background: #450A0A; color: #FCA5A5; }
        div[data-testid="stMetric"], div[data-testid="stVerticalBlockBorderWrapper"] {
            background: #121216;
            border: 1px solid #1F1F28;
            border-radius: 12px;
        }
        button[data-testid="stBaseButton-primary"] { background: #9333EA; border-color: #9333EA; color: #FFFFFF; }
        div[data-testid="column"]:has(.ticket-pin),
        div[data-testid="column"]:has(.radar-pin) {
            position: sticky;
            top: 0.6rem;
            align-self: start;
            max-height: 640px;
            overflow-y: auto;
        }
        .sn-card { animation: sn-in 0.35s ease; }
        @keyframes sn-in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
        @media (prefers-reduced-motion: reduce) { .sn-card { animation: none; } }
        .sn-board { background: rgba(18, 20, 31, 0.92); border: 1px solid rgba(139, 92, 246, 0.25); border-radius: 14px; padding: 0.8rem 0.9rem; margin-bottom: 0.8rem; }
        .sn-league-badge { display: inline-flex; align-items: center; gap: 6px; font-size: 0.95rem; font-weight: 800; letter-spacing: 0.05em; padding: 4px 12px; border-radius: 20px; }
        .sn-badge-nhl { background: rgba(6, 182, 212, 0.18); color: #38BDF8; border: 1px solid rgba(6, 182, 212, 0.4); }
        .sn-badge-nba { background: rgba(249, 115, 22, 0.18); color: #FB923C; border: 1px solid rgba(249, 115, 22, 0.4); }
        .sn-badge-nfl { background: rgba(16, 185, 129, 0.18); color: #34D399; border: 1px solid rgba(16, 185, 129, 0.4); }
        .sn-dot { height: 8px; width: 8px; background: rgba(255,255,255,0.25); border-radius: 50%; display: inline-block; margin: 0 3px; }
        .sn-dot.active { width: 22px; border-radius: 4px; background: #8B5CF6; }
        .sn-card { background: linear-gradient(135deg, rgba(25, 30, 48, 0.75), rgba(17, 20, 34, 0.9)); border: 1px solid rgba(255,255,255,0.07); border-radius: 10px; padding: 0.65rem 0.7rem; margin-bottom: 0.35rem; }
        .sn-team { display: flex; align-items: center; justify-content: space-between; margin: 3px 0; }
        .sn-team-info { display: flex; align-items: center; gap: 8px; }
        .sn-logo { width: 26px; height: 26px; object-fit: contain; }
        .sn-name { font-weight: 700; color: #F8FAFC; font-size: 0.9rem; }
        .sn-result { display: flex; align-items: center; gap: 8px; font-variant-numeric: tabular-nums; font-weight: 750; }
        .sn-w { color: #34D399; }
        .sn-l { color: #F43F5E; }
        .sn-record { color: #94A3B8; font-size: 0.72rem; }
        .sn-odds { display: flex; justify-content: space-between; gap: 6px; background: rgba(0,0,0,0.3); border-radius: 6px; padding: 4px 8px; margin-top: 6px; font-size: 0.75rem; font-variant-numeric: tabular-nums; color: #CBD5E1; }
        .sn-odds b { color: #A78BFA; }
        div[data-testid="stElementContainer"]:has(.tick-strip) {
            width: 100%;
            max-width: 100%;
            min-width: 0;
        }
        div[data-testid="stMarkdown"]:has(.tick-strip) { margin: 0; }
        div[data-testid="stMarkdown"]:has(.tick-strip) p { margin: 0; }
        html, body, .stApp { max-width: 100vw; }
        [data-testid="stMain"], section.stMain { overflow-x: hidden; }
        [data-testid="stHorizontalBlock"] { min-width: 0; }
        [data-testid="stColumn"], div[data-testid="column"] { min-width: 0; }
        .st-key-score-row,
        .st-key-score-row [data-testid="stHorizontalBlock"] {
            display: flex;
            flex-flow: row nowrap !important;
            align-items: stretch;
            gap: 12px;
            width: 100%;
            max-width: 100%;
            min-width: 0;
            overflow-x: scroll !important;
            overflow-y: hidden;
            padding: 4px 2px 12px;
            margin-bottom: 0.75rem;
            scroll-snap-type: x proximity;
            scrollbar-width: thin;
            scrollbar-color: #9333EA #1A1A22;
        }
        .st-key-score-row::-webkit-scrollbar,
        .st-key-score-row [data-testid="stHorizontalBlock"]::-webkit-scrollbar { height: 8px; }
        .st-key-score-row::-webkit-scrollbar-track,
        .st-key-score-row [data-testid="stHorizontalBlock"]::-webkit-scrollbar-track {
            background: #1A1A22;
            border-radius: 99px;
        }
        .st-key-score-row::-webkit-scrollbar-thumb,
        .st-key-score-row [data-testid="stHorizontalBlock"]::-webkit-scrollbar-thumb {
            background: #9333EA;
            border-radius: 99px;
        }
        /* About four cards fit across the row; the rest scroll sideways. */
        .st-key-score-row > div,
        .st-key-score-row [data-testid="stHorizontalBlock"] > div {
            flex: 0 0 calc((100% - 36px) / 4) !important;
            width: calc((100% - 36px) / 4) !important;
            min-width: 220px !important;
            max-width: none !important;
            scroll-snap-align: start;
        }
        .tick-strip { width: 100%; }
        .tick-strip > .tick,
        .st-key-score-row .tick {
            width: 100%;
            height: auto;
            min-height: 112px;
            box-sizing: border-box;
            padding: 10px 14px;
            border: 1px solid #1F1F28;
            border-radius: 12px;
            display: flex;
            flex-direction: column;
            justify-content: center;
            gap: 6px;
            background: #0A0A0C;
            transition: transform 180ms ease, border-color 180ms ease, background-color 180ms ease;
        }
        .tick-strip > .tick:hover {
            transform: translateY(-2px);
            border-color: #A855F7;
            background: #14141B;
        }
        .tick-strip .tick-status {
            display: flex;
            align-items: center;
            justify-content: flex-end;
            gap: 6px;
            font-size: 0.7rem;
            line-height: 14px;
            margin: 0;
            color: #9CA3AF;
            white-space: nowrap;
        }
        .tick-strip .tick-status.live { color: #FCA5A5; font-weight: 700; }
        .tick-strip .live-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #EF4444;
            animation: live-pulse 1.4s ease-in-out infinite;
        }
        @keyframes live-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
        .tick-strip .tick-row {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 10px;
            margin: 0;
            min-height: 34px;
        }
        .tick-strip .tick-left {
            display: flex;
            align-items: center;
            gap: 10px;
            min-width: 0;
            color: #FFFFFF;
        }
        .tick-strip .tick-name { display: flex; flex-direction: column; min-width: 0; line-height: 1.15; }
        .tick-strip .tick-name b { font-size: 0.88rem; font-weight: 750; white-space: nowrap; }
        .tick-strip .tick-name small { font-size: 0.7rem; color: #9CA3AF; white-space: nowrap; }
        .tick-strip .sn-result { font-size: 0.95rem; line-height: 1; gap: 6px; margin: 0; flex: 0 0 auto; }
        .tick-strip .tick-score { font-variant-numeric: tabular-nums; font-weight: 750; color: #FFFFFF; }
        .tick-strip img,
        div[data-testid="stMarkdown"]:has(.tick-strip) img,
        div[data-testid="stElementContainer"]:has(.tick-strip) img {
            width: 28px !important;
            height: 28px !important;
            max-width: 32px !important;
            max-height: 32px !important;
            min-height: 0 !important;
            object-fit: contain !important;
            flex: 0 0 28px;
            display: inline-block;
        }
        .team-badge {
            display: inline-flex;
            align-items: center;
            justify-content: center;
            flex: 0 0 28px;
            border-radius: 50%;
            background: #1F1F28;
            border: 1px solid #2E2E3A;
            color: #E5E7EB;
            font-size: 0.6rem;
            font-weight: 800;
            letter-spacing: 0;
        }
        .st-key-score-row button { width: 100%; }
        .st-key-league-seg { margin-bottom: 0.25rem; }
        .st-key-league-seg button { white-space: nowrap; min-width: 5.5rem; }
        .sn-dot { transition: width 200ms ease, background-color 200ms ease; }
        /* Action buttons brighten on hover and never move. */
        .stButton > button, .stDownloadButton > button, button[data-testid^="stBaseButton"] {
            transition: filter 160ms ease, background-color 160ms ease, border-color 160ms ease;
            transform: none !important;
        }
        .stButton > button:hover, .stDownloadButton > button:hover, button[data-testid^="stBaseButton"]:hover {
            filter: brightness(1.18);
            transform: none !important;
        }
        @media (prefers-reduced-motion: reduce) {
            *, *::before, *::after {
                animation: none !important;
                transition: none !important;
                scroll-behavior: auto !important;
            }
            .tick-strip > .tick:hover { transform: none; }
        }
        .ticket-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(112px, 1fr)); gap: 0.45rem; margin: 0.35rem 0 0.65rem; }
        .ticket-stats div { background: #0A0A0C; border: 1px solid #1F1F28; border-radius: 10px; padding: 0.4rem 0.55rem; }
        .ticket-stats span { display: block; color: #9CA3AF; font-size: 0.72rem; }
        .ticket-stats b { display: block; overflow-wrap: anywhere; margin-top: 0.12rem; color: #FFFFFF; font-size: 1.05rem; font-variant-numeric: tabular-nums; }
        .ticket-stats b.up { color: #86EFAC; }
        .ticket-stats b.down { color: #FCA5A5; }
        .ticket-note { color: #9CA3AF; font-size: 0.8rem; line-height: 1.4; margin: 0.1rem 0 0.45rem; }
        .leg { border-top: 1px solid #1F1F28; padding: 0.55rem 0 0.15rem; }
        .leg-top { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
        .leg-top b { color: #FFFFFF; font-size: 0.95rem; }
        .leg-top span { color: #9CA3AF; font-size: 0.78rem; white-space: nowrap; }
        .leg-stats { display: flex; flex-wrap: wrap; gap: 0.35rem 0.9rem; margin-top: 0.28rem; color: #D1D5DB; font-size: 0.8rem; }
        .leg-stats b { color: #FFFFFF; font-variant-numeric: tabular-nums; }
        .leg-stats b.up { color: #86EFAC; }
        .leg-stats b.down { color: #FCA5A5; }
        .leg-why { color: #9CA3AF; font-size: 0.75rem; line-height: 1.35; margin: 0.15rem 0 0.65rem; }
        div[class*="st-key-who-"] { margin-top: -0.15rem; margin-bottom: 0.15rem; }
        div[class*="st-key-who-"] button {
            min-height: 1.55rem;
            padding: 0 0.6rem;
            border-radius: 999px;
            background: #1A1A22;
            border: 1px solid #2E2E3A;
            color: #E5E7EB;
            font-size: 0.75rem;
        }
        div[data-testid="stDialog"] div[role="dialog"] { background: #121216; border: 1px solid #1F1F28; color: #FFFFFF; }
        .profile-head { color: #FFFFFF; font-weight: 700; font-size: 0.9rem; margin: 0.55rem 0 0; }
        .profile-head span { color: #9CA3AF; font-weight: 500; font-size: 0.78rem; margin-left: 0.4rem; }
        .profile-log { overflow-x: auto; border: 1px solid #1F1F28; border-radius: 10px; margin: 0.35rem 0 0.65rem; }
        .profile-log table { width: 100%; border-collapse: collapse; font-size: 0.8rem; font-variant-numeric: tabular-nums; }
        .profile-log th { background: #1A1A22; color: #9CA3AF; font-weight: 650; font-size: 0.72rem; padding: 0.4rem 0.5rem; text-align: center; white-space: nowrap; }
        .profile-log td { color: #E5E7EB; padding: 0.38rem 0.5rem; text-align: center; border-top: 1px solid #1F1F28; white-space: nowrap; background: #0A0A0C; }
        .profile-log th:nth-child(-n+2), .profile-log td:nth-child(-n+2) { text-align: left; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    # The 100% Hit Rates card styles live next to the card code and reuse the same palette.
    st.markdown(f"<style>{HIT_RATES_CSS}</style>", unsafe_allow_html=True)


def format_start(value: str) -> str:
    if not value:
        return "TBD"
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    local = parsed.astimezone()
    clock = local.strftime("%I:%M %p").lstrip("0")
    return f"{local.strftime('%b %d')}, {clock}"


TIPS = {
    "% it'll hit": "The model's chance this bet wins. A parlay multiplies the legs.",
    "Expected value": "Average profit per dollar if this bet were placed many times. Positive means the payout is bigger than the chance.",
    "Fair": "The chance after the sportsbook's margin is removed.",
    "EV": "Expected value on this one leg.",
    "In odds band": "The payout landed inside this tier's target odds range.",
    "Outside odds band": "The payout is outside this tier's target odds range.",
    "Kelly suggestion": "A smaller stake based on the edge and the odds. It is a suggestion, not a limit.",
    "entry edge": "Whole-ticket edge: the ticket's hit chance times its payout multiple, minus 1.",
    "Together": "The leg chances multiplied. This is the parlay's chance to hit.",
    "Season": "How often this stat happened across the logged season.",
    "Last 5": "How often it happened in the last five logged games.",
    "vs opponent": "The season rate nudged for how much tonight's opponent scores or allows versus the league.",
    "Odds": "The price. American odds, then decimal.",
    "Payout": "Stake times the decimal odds, including the stake.",
    "Profit": "Payout minus the stake.",
    "Profit if win": "What you keep if the ticket wins.",
    "Wager": "The cash amount used for the payout.",
    "Ticket price": "The odds you were offered. This sets the payout. It does not change the hit chance.",
    "ATS": "Against the spread. The team beat the point spread. Winning the game is not enough if they were favored by more than the final margin.",
    "O/U": "Over/under. The two teams' scores added together, compared with the total the book posted.",
    "Over": "The score or the stat finished higher than the number.",
    "Under": "The score or the stat finished lower than the number.",
    "Moneyline": "A bet on which team wins. No point spread.",
    "Spread": "The favorite has to win by more than this number. The underdog can lose by fewer than this number and still cover.",
    "Favorite": "The team the sportsbook expects to win.",
}


def tip(word: str) -> str:
    text = TIPS.get(word, "")
    return f'<span title="{text}">{word}</span>'


def _matchup_sides(matchup: str) -> list[str]:
    if " @ " not in matchup:
        return []
    away, home = [part.strip() for part in matchup.split(" @ ", 1)]
    return [side for side in (away, home) if side and not side.isdigit()]


def bubble(name: str, key: str, sport: str = "") -> None:
    if not name or name in {"—", "Typed odds"}:
        return
    if st.button(name, key=key):
        st.session_state.entity_popup = name
        st.session_state.entity_sport = sport


WATCH = (
    ("points", "PTS"),
    ("totalRebounds", "REB"),
    ("assists", "AST"),
    ("threePointFieldGoalsMade", "3PM"),
    ("goals", "G"),
    ("shotsTotal", "SOG"),
    ("rushingYards", "RUSH"),
    ("receivingYards", "REC"),
    ("passingYards", "PASS"),
    ("receptions", "REC"),
)


def _season_line(row: dict) -> str:
    totals = row["totals"]
    games = max(int(row["games"]), 1)
    bits = []
    for key, label in WATCH:
        if key in totals:
            bits.append(f"{label} {totals[key] / games:.1f}")
    stat_line = " · ".join(bits[:6]) if bits else "No counting stats in this feed"
    return f"{row['season']} · {row['games']} games · {stat_line}"


def _profile_tiles(title: str, tiles: list[tuple[str, str]], note: str = "") -> None:
    if not tiles:
        return
    cells = "".join(f"<div><span>{html.escape(label)}</span><b>{html.escape(value)}</b></div>" for label, value in tiles)
    aside = f"<span>{html.escape(note)}</span>" if note else ""
    st.markdown(
        f"<div class='profile-head'>{html.escape(title)}{aside}</div><div class='ticket-stats'>{cells}</div>",
        unsafe_allow_html=True,
    )


def _profile_games(games: dict) -> None:
    if not games or not games.get("rows"):
        return
    head = "".join(f"<th>{html.escape(column)}</th>" for column in games["columns"])
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>" for row in games["rows"]
    )
    count = len(games["rows"])
    st.markdown(
        f"<div class='profile-head'>{'Last game' if count == 1 else f'Last {count} games'}</div>"
        f"<div class='profile-log'><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True,
    )


def _clear_entity_popup() -> None:
    st.session_state.entity_popup = ""


@st.dialog("Profile", width="large", on_dismiss=_clear_entity_popup)
def _entity_dialog(name: str) -> None:
    title, closer = st.columns([8, 1])
    title.markdown(f"### {name}")
    if closer.button("✕", key="close-entity"):
        st.session_state.entity_popup = ""
        st.rerun()
    matches = search_entities(name)
    wanted = str(st.session_state.get("entity_sport") or "").upper()
    if wanted:
        same_sport = [row for row in matches if row["sport"] == wanted]
        matches = same_sport or matches
    entity = next((row for row in matches if row["name"].casefold() == name.casefold() or row.get("team", "").casefold() == name.casefold()), None)
    if entity is None and matches:
        entity = matches[0]
    if entity is None:
        st.caption("No NFL, NBA, or NHL profile for that name.")
        return
    st.caption(f"{entity['sport']} · {entity.get('team') or entity['kind'].title()}")
    if entity["kind"] == "team":
        try:
            from src.api.espn_client import fetch_standings

            table = fetch_standings(entity["sport"])
        except Exception:
            table = {}
        profile = next((item for abbrev, item in table.items() if abbrev.upper() == name.upper() or item.name.casefold() == name.casefold()), None)
        if profile is None:
            st.caption("No standings row for this team.")
            return
        st.write(f"Record {profile.wins}-{profile.losses}-{profile.ot_losses}")
        st.write(f"Scores {profile.gf_per_game:.1f} · Allows {profile.ga_per_game:.1f}")
        st.caption("The standings feed is the current season.")
        return
    bundle = load_player(entity)
    card: dict = {}
    if entity.get("league") == "nhl" and bundle.get("nhl_id"):
        card = nhl_profile(load_nhl_landing(bundle["nhl_id"]))
        if not card["season_tiles"] and not card["career"]:
            card = {}
    if not card:
        card = espn_profile(bundle.get("gamelog") or {})
    if not card:
        st.caption("No season line in the feed.")
        return
    _profile_tiles(card["season"], card["season_tiles"], "totals")
    _profile_tiles("Per game", card["per_game"])
    _profile_tiles("Career", card["career"], "regular season")
    _profile_games(card["games"])
    if card["career"]:
        return
    past = card.get("past") or []
    if not past:
        st.caption("Career totals aren't in this feed.")
        return
    if st.checkbox("Past seasons", key=f"past-{entity['espn_id']}"):
        for row in past:
            st.write(_season_line(row))


def money(value: float) -> str:
    return format_money(value)


def cash(value: float) -> str:
    return money(value).replace("$", "&#36;")


def percent(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.1f}%"


def status_pill(status: str) -> str:
    kind = {"WON": "good", "LOST": "bad", "PENDING": "warn"}.get(status, "")
    return f'<span class="pill {kind}">{status}</span>'


def _chart_layout(fig: go.Figure, height: int = 360) -> go.Figure:
    fig.update_layout(
        template="plotly_dark",
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin=dict(l=16, r=16, t=36, b=16),
        font=dict(color="#E7EEF8"),
    )
    return fig


def exposure_gauge(exposure: float) -> go.Figure:
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number",
            value=exposure * 100,
            number={"suffix": "%", "font": {"size": 28}},
            title={"text": "Stake / bankroll"},
            gauge={
                "axis": {"range": [0, 10]},
                "bar": {"color": "#C026FF"},
                "steps": [
                    {"range": [0, 3], "color": "#123524"},
                    {"range": [3, 5], "color": "#3F3414"},
                    {"range": [5, 10], "color": "#4A1D1D"},
                ],
                "threshold": {"line": {"color": "#FDE68A", "width": 3}, "thickness": 0.8, "value": 5},
            },
        )
    )
    return _chart_layout(fig, height=230)


@st.cache_resource
def _scheduler_started() -> bool:
    if os.getenv("PUCKEDGE_DISABLE_SCHEDULER") == "1":
        return False
    start_scheduler()
    return True


def book_label(key: str) -> str:
    return BOOK_LABELS.get(key, key.replace("_", " ").replace("-", " ").title())


@st.cache_data(ttl=600, show_spinner=False)
def cached_update_status() -> str:
    if os.getenv("PUCKEDGE_DISABLE_UPDATES") == "1":
        return ""
    return peek_update(ROOT)


def current_board(slate_date: date, live: bool, odds_key: str, sportsbook: str, sport: str, refresh: bool) -> PricedBoard:
    token = (slate_date.isoformat(), live, bool(odds_key), sportsbook, sport)
    if refresh or st.session_state.get("board_token") != token:
        if refresh:
            st.session_state.pop("sport_boards", None)
        with st.spinner(f"Loading the {sport} slate and pricing the board..."):
            st.session_state.board = load_board(
                slate_date.isoformat(),
                prefer_live=live,
                odds_api_key=odds_key or None,
                sportsbook=sportsbook,
                sport=sport,
            )
        st.session_state.board_token = token
        st.session_state.tickets = []
    return st.session_state.board


def _book_options() -> list[str]:
    previous = st.session_state.get("board")
    options = list(getattr(previous, "available_books", None) or ["bet365", "draftkings", "fanduel", "pinnacle", "synthetic-bet365"])
    if "sportsbook" not in st.session_state:
        st.session_state.sportsbook = "bet365"
    if st.session_state.sportsbook not in options:
        options = [st.session_state.sportsbook, *[book for book in options if book != st.session_state.sportsbook]]
    return options


def render_top(account: dict) -> str:
    brand, nav = st.columns([1, 3.6])
    with brand:
        st.markdown('<h1 class="brand">DARK<span>NODE</span></h1>', unsafe_allow_html=True)
    with nav:
        st.markdown(NAV_CSS, unsafe_allow_html=True)
        chosen = st.radio("Page", NAV_OPTIONS, horizontal=True, key="nav_page", label_visibility="collapsed")
    page = {NAV_VAULT: "Record", NAV_SETUP: "Setup", NAV_OMNI: "Omni"}.get(chosen, "Play")
    st.session_state["_signed_in_name"] = account.get("name", "")
    return page


def _is_owner(account: dict | None) -> bool:
    return str((account or {}).get("username", "")).lower() == OWNER_USERNAME


def _save_sharp_key(value: str) -> None:
    cleaned = value.strip()
    if not cleaned:
        return
    path = ROOT / ".env"
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    updated = False
    kept: list[str] = []
    for line in lines:
        if line.startswith("SHARPAPI_KEY="):
            kept.append(f"SHARPAPI_KEY={cleaned}")
            updated = True
        else:
            kept.append(line)
    if not updated:
        kept.append(f"SHARPAPI_KEY={cleaned}")
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.environ["SHARPAPI_KEY"] = cleaned


def render_setup(account: dict) -> None:
    options = _book_options()
    st.radio("Sport", ["NHL", "NFL", "NBA"], horizontal=True, key="sport")
    st.selectbox("Book", options, format_func=book_label, key="sportsbook")
    saved_limit = load_max_stake(_limit_user())
    if saved_limit is not None and float(st.session_state.get("target_stake", 0.0) or 0.0) > saved_limit:
        st.session_state.target_stake = saved_limit
    st.number_input(
        "Stake",
        min_value=0.0,
        max_value=saved_limit,
        value=min(25.0, saved_limit) if saved_limit is not None else 25.0,
        step=5.0,
        key="target_stake",
    )
    limit_input = st.number_input(
        "Most you will stake on one ticket ($)",
        min_value=1.0,
        value=float(saved_limit or 25.0),
        step=1.0,
        key="limit_editor",
    )
    if st.button("Save limit"):
        save_max_stake(_limit_user(), float(limit_input))
        st.rerun()
    st.date_input("Date", value=date.today(), key="slate_date")
    st.toggle("Live feed", value=os.getenv("PUCKEDGE_LIVE", "1") != "0", key="live_feed")
    if _is_owner(account):
        st.caption("SharpAPI key is saved on this computer." if os.getenv("SHARPAPI_KEY", "").strip() else "No SharpAPI key saved yet.")
        st.text_input("SharpAPI key", value="", type="password", key="sharp_key_input")
        if st.button("Save SharpAPI key"):
            typed = str(st.session_state.get("sharp_key_input") or "").strip()
            if typed:
                _save_sharp_key(typed)
                st.session_state.pop("sharp_key_input", None)
                st.rerun()
            else:
                st.warning("Paste a key first.")
    session = open_session()
    try:
        balance = get_balance(session)
    finally:
        session.close()
    st.caption(f"Bankroll {money(balance)}")
    if "bankroll_editor" not in st.session_state:
        st.session_state.bankroll_editor = float(balance)
    bankroll_input = st.number_input("Set bankroll", min_value=0.0, step=50.0, key="bankroll_editor")
    if st.button("Update bankroll"):
        editor = open_session()
        try:
            set_balance(editor, float(bankroll_input))
        finally:
            editor.close()
        st.rerun()
    if st.button("Refresh slate"):
        st.session_state.force_refresh = True
        st.rerun()
    update_line = cached_update_status()
    if update_line:
        st.caption(update_line)
    st.caption(f"Signed in as {account.get('name', '')}")
    if st.button("Log out"):
        st.session_state.account = None
        st.session_state.owner_unlocked = False
        st.session_state.owner_override = False
        st.session_state.skip_auto_login = True
        st.rerun()
    if _is_owner(account):
        render_owner_log()


def _limit_user() -> str:
    return str((st.session_state.get("account") or {}).get("username", ""))


def _skip_limits() -> None:
    st.session_state.limits_skipped = True


@st.dialog("Set your limits", on_dismiss=_skip_limits)
def _limits_dialog(balance: float) -> None:
    st.caption("DarkNode sizes every ticket to these numbers. You can change them later in Setup.")
    bankroll = st.number_input("Bankroll ($)", min_value=0.0, value=float(balance), step=10.0, key="limits_bankroll")
    default_limit = round(min(25.0, bankroll) if bankroll > 0 else 25.0, 2)
    limit = st.number_input("Most you will stake on one ticket ($)", min_value=0.0, value=default_limit, step=1.0, key="limits_max")
    if bankroll > 0 and limit > bankroll:
        st.warning("Your limit is above your bankroll. Tickets will be capped at the bankroll.")
    save, skip = st.columns(2)
    if save.button("Save limits", type="primary"):
        if limit <= 0:
            st.error("Enter a stake limit above $0.")
            return
        if abs(bankroll - balance) > 0.005:
            editor = open_session()
            try:
                set_balance(editor, float(bankroll))
            finally:
                editor.close()
        save_max_stake(_limit_user(), min(limit, bankroll) if bankroll > 0 else limit)
        st.session_state.limits_skipped = True
        st.rerun()
    if skip.button("Ask me later"):
        st.session_state.limits_skipped = True
        st.rerun()


def current_controls() -> dict:
    _book_options()
    session = open_session()
    try:
        balance = get_balance(session)
    finally:
        session.close()
    refresh = bool(st.session_state.pop("force_refresh", False))
    max_stake = load_max_stake(_limit_user())
    if max_stake is not None and balance > 0:
        max_stake = min(max_stake, balance)
    wanted = float(st.session_state.get("target_stake", max_stake or 25.0))
    return {
        "max_stake": max_stake,
        "date": st.session_state.get("slate_date", date.today()),
        "live": bool(st.session_state.get("live_feed", os.getenv("PUCKEDGE_LIVE", "1") != "0")),
        "odds_key": os.getenv("SHARPAPI_KEY", "").strip(),
        "sportsbook": st.session_state.sportsbook,
        "sport": st.session_state.get("sport", "NHL"),
        "refresh": refresh,
        "balance": balance,
        "stake": capped_stake(wanted, max_stake),
    }


def _starters(board: PricedBoard, abbrev: str) -> str:
    names = [goalie.name for goalie in board.context.goalies if goalie.team_abbrev == abbrev and goalie.projected_starter]
    if not names:
        names = [goalie.name for goalie in board.context.goalies if goalie.team_abbrev == abbrev]
    return names[0] if names else "TBD"


def _record(board: PricedBoard, abbrev: str) -> str:
    team = board.context.teams.get(abbrev)
    if team is None:
        return ""
    streak = f"{team.streak_code}{team.streak_count}" if team.streak_code else "—"
    rest = "B2B" if team.back_to_back else (f"{team.rest_days}d rest" if team.rest_days is not None else "")
    return f"{team.wins}-{team.losses}-{team.ot_losses} · L10 {team.l10_wins}-{team.l10_losses}-{team.l10_ot} · {streak} {rest}".strip()


def render_board(board: PricedBoard) -> None:
    sport = board.context.sport
    st.subheader(f"Today's {sport} board")
    if not board.context.games:
        st.warning(f"No {sport} games on this date.")
        return
    for note in board.context.notes:
        st.info(note)
    rows = []
    for game in board.context.games:
        home = board.context.teams.get(game.home.abbrev)
        away = board.context.teams.get(game.away.abbrev)
        prices = [leg for leg in board.legs if leg.game_id == game.game_id and leg.market_type == "MONEYLINE"]
        home_ml = next((leg for leg in prices if leg.side == "HOME"), None)
        away_ml = next((leg for leg in prices if leg.side == "AWAY"), None)
        total = next(
            (
                leg
                for leg in board.legs
                if leg.game_id == game.game_id and leg.market_type == "TOTAL" and leg.side == "OVER"
                and (sport != "NHL" or leg.line in {5.5, 6.0, 6.5})
            ),
            None,
        )
        spread = next(
            (
                leg
                for leg in board.legs
                if leg.game_id == game.game_id and leg.market_type in {"SPREAD", "PUCKLINE"} and leg.side == "HOME"
            ),
            None,
        )
        if sport == "NHL":
            away_label = f"{game.away.name} ({_starters(board, game.away.abbrev)})"
            home_label = f"{game.home.name} ({_starters(board, game.home.abbrev)})"
        else:
            away_label = game.away.name
            home_label = game.home.name
        rows.append(
            {
                "Start": format_start(game.start_time_utc),
                "Matchup": f"{game.away.abbrev} @ {game.home.abbrev}",
                "State": game.game_state,
                "Away": away_label,
                "Home": home_label,
                "Form": f"{away.abbrev if away else game.away.abbrev}: {_record(board, game.away.abbrev)} | {home.abbrev if home else game.home.abbrev}: {_record(board, game.home.abbrev)}",
                "Spread": (
                    f"{spread.selection} · {percent(spread.model_prob)} it'll hit" if spread else "—"
                ),
                "Moneyline": (
                    f"{game.away.abbrev} {format_american(away_ml.american_odds) if away_ml else '—'} ({percent(away_ml.model_prob) if away_ml else '—'}) / "
                    f"{game.home.abbrev} {format_american(home_ml.american_odds) if home_ml else '—'} ({percent(home_ml.model_prob) if home_ml else '—'})"
                ),
                "Total": f"O/U {total.line:.1f} ({format_american(total.american_odds)})" if total else "—",
                "Signals": ", ".join(board.badges.get(game.game_id) or []) or "—",
                "Book": (home_ml or away_ml or total).bookmaker if (home_ml or away_ml or total) else board.odds_source,
            }
        )
    frame = pd.DataFrame(rows)
    st.dataframe(frame, width="stretch", hide_index=True)
    st.caption(f"Odds source: {board.odds_source}. Goalies are the current games-started leader until a starter is confirmed.")
    names = []
    for game in board.context.games:
        names.extend([game.away.name, game.home.name])
    for leg in board.legs:
        if leg.market_type not in {"MONEYLINE", "SPREAD", "PUCKLINE", "TOTAL", "TEAM_TOTAL"}:
            names.append(leg.target_entity)
    options = ["Choose a player or team from this board", *dict.fromkeys(names)]
    opened = st.selectbox("Open in the picker", options, key="board-open")
    if opened != options[0] and st.button("Open in the picker", key="board-open-go"):
        st.session_state.picker_autoload = opened
        st.rerun()


def render_builder(boards: list[PricedBoard], balance: float, stake: float) -> None:
    st.subheader("Parlay builder")
    board = boards[0]
    games = [(item.context.sport, game) for item in boards for game in item.context.games]
    legs = [leg for item in boards for leg in item.legs]
    badges: dict = {}
    for item in boards:
        badges.update(item.badges)
    league_label = " · ".join(item.context.sport for item in boards)
    descriptions = {
        "Conservative": "2 legs · about -120 to +160 · +1.5, team over 1.5, star 2+ shots · quarter Kelly, 2–3% of bankroll.",
        "Balanced": "2–5 legs · about 40–45% to hit · +110 to +250 · favorite moneylines, totals, shot and point props · eighth Kelly, 1–1.5%.",
        "Aggressive": "4–6 legs · +600 to +1500 · -1.5, dogs, anytime goals · about 0.5% of bankroll.",
        "Moonshot": "6–8 legs · +2000 to +10000 · multi-goal and plus-money props · 0.1–0.25%, suggestion capped at $10.",
        "Krazy": "4–8 legs · $1 returns $100 or more · dogs, anytime goals, wild props. The suggestion is a tiny slice of bankroll. Your target wager can be anything.",
    }
    if not games:
        st.warning(f"No {league_label} games on this date.")
        return
    if league_label != "NHL":
        st.caption(f"{league_label} tickets use moneylines, spreads, totals, and player props.")
    tier = st.radio(
        "Risk profile",
        list(PROFILES),
        horizontal=True,
        format_func=lambda key: "KRAZY MODE" if key == "Krazy" else key,
    )
    st.markdown(f"<p class='fine'>{descriptions[tier]}</p>", unsafe_allow_html=True)
    gauge_col, guide_col = st.columns([1.1, 1.4])
    profile = PROFILES[tier]
    with gauge_col:
        st.plotly_chart(exposure_gauge(exposure_pct(stake, balance)), width="stretch")
    with guide_col:
        st.markdown("**Sizing guide**")
        low = balance * profile.stake_low
        high = balance * profile.stake_high
        cap = f" · hard cap {cash(profile.stake_cap)}" if profile.stake_cap else ""
        st.write(f"Guideline for this tier: {cash(low)} to {cash(high)}{cap}.")
        exposure_problem = exposure_message(stake, balance)
        if exposure_problem:
            st.info(exposure_problem)
        else:
            st.write(f"Your wager is {exposure_text(stake, balance)} of bankroll.")
        if tier in {"Aggressive", "Moonshot", "Krazy"} and exposure_pct(stake, balance) > 0.05:
            st.warning("This stake is over 5% of bankroll on a longshot tier. The cards below show the fractional Kelly suggestion.")

    game_frame = pd.DataFrame(
        [
            {
                "Include": True,
                "League": sport_name,
                "Matchup": f"{game.away.abbrev} @ {game.home.abbrev}",
                "Start": format_start(game.start_time_utc),
                "Signals": ", ".join(badges.get(game.game_id) or []) or "—",
                "game_id": game.game_id,
            }
            for sport_name, game in games
        ]
    )
    edited = st.data_editor(
        game_frame,
        column_config={
            "Include": st.column_config.CheckboxColumn("Include", default=True),
            "game_id": None,
        },
        disabled=["League", "Matchup", "Start", "Signals"],
        hide_index=True,
        width="stretch",
        key=f"games-{league_label}-{board.context.slate_date}-{board.odds_source}",
    )
    selected = set(edited.loc[edited["Include"], "game_id"])
    if st.button("Generate optimal parlays", type="primary"):
        if len(selected) < 1:
            st.warning("Include at least one game.")
        else:
            with st.spinner("Searching correlated tickets..."):
                track("generate", league_label, f"{tier} parlays")
                st.session_state.tickets = generate_parlays(
                    legs,
                    tier,
                    bankroll=balance,
                    stake=stake,
                    game_ids=selected,
                    max_stake=load_max_stake(_limit_user()),
                )
            st.session_state.ticket_tier = tier

    tickets: list[BuiltParlay] = st.session_state.get("tickets") or []
    if not tickets:
        st.caption("Generate a tier to see the top three tickets, with odds, edge, and a save button.")
        return
    if st.session_state.get("ticket_tier") != tier:
        st.caption("These cards are from the previous tier. Generate again to rebuild them.")
    for index, ticket in enumerate(tickets, start=1):
        render_ticket(ticket, index, board.odds_source)


def _leg_names(leg: MarketLeg) -> list[str]:
    names: list[str] = []
    for name in [leg.target_entity, *_matchup_sides(leg.matchup)]:
        if not name or name in names or name in {"—", "Typed odds"} or "@" in name:
            continue
        names.append(name)
    return names


def _stat(label: str, value: str, kind: str = "") -> str:
    shown = html.escape(value).replace("$", "&#36;")
    klass = f" class='{kind}'" if kind else ""
    return f"<div><span title=\"{html.escape(TIPS.get(label, ''))}\">{html.escape(label)}</span><b{klass}>{shown}</b></div>"


def _leg_head(leg: MarketLeg) -> str:
    estimated = (
        "<span class='pill warn' title=\"The book did not post this line. The price is an estimate and adds no edge.\">"
        "Estimated price - no live line</span>"
        if leg.bookmaker == "synthetic-bet365"
        else ""
    )
    return (
        "<div class='leg'>"
        "<div class='leg-top'>"
        f"<b>{html.escape(leg.selection)}</b>"
        f"<span>{html.escape(leg.matchup)}</span>"
        f"</div>{estimated}</div>"
    )


def _leg_detail(leg: MarketLeg) -> str:
    ev_kind = "up" if leg.ev >= 0 else "down"
    why = html.escape(leg.rationale or "").replace("$", "&#36;")
    chance = html.escape(TIPS["% it'll hit"])
    return (
        "<div class='leg-stats'>"
        f"<span title=\"{html.escape(TIPS['Odds'])}\">Odds <b>{html.escape(format_american(leg.american_odds))}</b></span>"
        f"<span title=\"{chance}\">Chance <b>{percent(leg.model_prob)}</b></span>"
        f"<span title=\"{html.escape(TIPS['Fair'])}\">Fair <b>{percent(leg.fair_prob)}</b></span>"
        f"<span title=\"{html.escape(TIPS['EV'])}\">EV <b class='{ev_kind}'>{percent(leg.ev)}</b></span>"
        "</div>"
        f"<div class='leg-why'>{why}</div>"
    )


def render_ticket(ticket: BuiltParlay, index: int, odds_source: str) -> None:
    with st.container(border=True):
        st.markdown(f"### Ticket {index} · {ticket.risk_tier}")
        flags = []
        if ticket.within_odds_band:
            flags.append(f'<span class="pill good" title="{html.escape(TIPS["In odds band"])}">In odds band</span>')
        else:
            flags.append(f'<span class="pill warn" title="{html.escape(TIPS["Outside odds band"])}">Outside odds band</span>')
        if ticket.filters_relaxed:
            flags.append('<span class="pill warn">Floor relaxed</span>')
        if ticket.synergy_score:
            flags.append(f'<span class="pill good">Synergy {ticket.synergy_score}</span>')
        st.markdown("".join(flags), unsafe_allow_html=True)
        ev_kind = "up" if ticket.ev >= 0 else "down"
        st.markdown(
            "<div class='ticket-stats'>"
            + _stat("Odds", format_american(ticket.american_odds))
            + _stat("% it'll hit", percent(ticket.model_prob))
            + _stat("Expected value", percent(ticket.ev), ev_kind)
            + _stat("Payout", money(ticket.potential_payout))
            + _stat("Profit if win", money(ticket.net_profit_if_win))
            + "</div>",
            unsafe_allow_html=True,
        )
        if ticket.risk_tier == "Krazy":
            st.markdown(
                f'<span class="pill warn">&#36;1 returns {cash(ticket.decimal_odds)}</span>',
                unsafe_allow_html=True,
            )
        for note in ticket.synergy_notes:
            st.caption(note)
        if len(ticket.legs) > len({leg.game_id for leg in ticket.legs}):
            st.caption("Some of these legs are from the same game. The chance multiplies them as if they were separate. A book may pay less for that combo.")
        if ticket.exposure_warning:
            st.warning(ticket.exposure_warning)
        books = ", ".join(sorted({book_label(leg.bookmaker) for leg in ticket.legs})) or book_label(odds_source)
        st.markdown(
            "<div class='ticket-note'>"
            f"<span title=\"{html.escape(TIPS['Kelly suggestion'])}\">Kelly suggestion</span> {cash(ticket.recommended_stake)} "
            f"({percent(ticket.recommended_pct if ticket.recommended_pct is not None else ticket.kelly_fraction)} of bankroll). "
            f"Guideline {cash(ticket.guideline_low)}–{cash(ticket.guideline_high)}. "
            f"<span title=\"{html.escape(TIPS['entry edge'])}\">Entry edge</span> {percent(ticket.entry_edge)}. "
            f"Prices from {html.escape(books)}."
            "</div>",
            unsafe_allow_html=True,
        )
        for leg_index, leg in enumerate(ticket.legs):
            st.markdown(_leg_head(leg), unsafe_allow_html=True)
            names = _leg_names(leg)
            if names:
                with st.container(horizontal=True, gap="small", vertical_alignment="center", key=f"who-{index}-{leg_index}"):
                    for name in names:
                        slug = "".join(ch.lower() if ch.isalnum() else "-" for ch in name).strip("-")
                        bubble(name, f"ticket-{index}-{leg_index}-{slug}")
            st.markdown(_leg_detail(leg), unsafe_allow_html=True)
        signature = tuple(leg.leg_id for leg in ticket.legs) + (ticket.risk_tier, round(ticket.stake, 2))
        saved = st.session_state.setdefault("saved_signatures", set())
        if signature in saved:
            st.success("This ticket is already in the ledger.")
            return
        left, right = st.columns(2)
        if left.button("Save & Track (Paper)", key=f"paper-{index}-{ticket.american_odds}"):
            _persist(ticket, placed_real=False, odds_source=odds_source, signature=signature)
        if right.button("Log as Real Wager", key=f"real-{index}-{ticket.american_odds}"):
            _persist(ticket, placed_real=True, odds_source=odds_source, signature=signature)
        if st.button("Add these legs to the slip", key=f"slip-add-{index}-{ticket.american_odds}"):
            for leg in ticket.legs:
                _add_quote(
                    {
                        "id": leg.leg_id,
                        "sport": getattr(leg, "sport", None) or st.session_state.get("sport", "NHL"),
                        "player": leg.target_entity,
                        "selection": leg.selection,
                        "probability": leg.model_prob,
                        "sample": leg.rationale,
                        "l5": "",
                        "season_avg": None,
                        "american": leg.american_odds,
                        "decimal": leg.decimal_odds,
                        "book": leg.bookmaker,
                        "best_note": "",
                        "matchup": leg.matchup,
                        "game_id": leg.game_id,
                        "rationale": leg.rationale,
                        "game_key": f"{getattr(leg, 'sport', None) or st.session_state.get('sport', 'NHL')}|{leg.matchup}",
                        "market_type": leg.market_type,
                        "market_key": leg.market_key,
                        "pick": leg.side,
                        "line": leg.line,
                        "team": leg.team_abbrev or "",
                    }
                )
            st.rerun()


def _persist(ticket: BuiltParlay, *, placed_real: bool, odds_source: str, signature: tuple, sport: str | None = None) -> None:
    leg_leagues = {str(getattr(leg, "sport", None) or "").upper() for leg in ticket.legs} - {""}
    if sport is None and leg_leagues:
        sport = next(iter(leg_leagues)) if len(leg_leagues) == 1 else "MIX"
    session = open_session()
    try:
        save_parlay(session, ticket, placed_real=placed_real, odds_source=odds_source, sport=sport or st.session_state.get("sport", "NHL"))
    except ValueError as exc:
        st.error(str(exc))
        return
    finally:
        session.close()
    st.session_state.saved_signatures.add(signature)
    label = "real wager" if placed_real else "paper ticket"
    st.toast(f"Logged {label}.")
    st.rerun()


def render_ledger() -> None:
    st.subheader("Performance tracker")
    if st.button("Sync & Auto-Settle All", type="primary"):
        with st.spinner("Reading final box scores..."):
            updated = run_settlement_once()
        st.success(f"Checked pending tickets. {updated} status change(s).")
    session = open_session()
    try:
        rows = list_parlays(session)
    finally:
        session.close()
    if not rows:
        st.info("No tickets yet. Build one and save it as paper or as a real wager.")
        return
    table = pd.DataFrame(
        [
            {
                "When": row.created_at.astimezone().strftime("%b %d, %I:%M %p"),
                "Sport": getattr(row, "sport", None) or "NHL",
                "Tier": row.risk_tier,
                "% it'll hit": percent(row.model_prob),
                "Odds": format_american(row.total_american_odds),
                "Stake": money(row.stake_amount),
                "Payout": money(row.potential_payout),
                "Status": row.status,
                "Profit": money(row.net_profit),
                "Mode": "Real" if row.user_placed_bet else "Paper",
            }
            for row in rows
        ]
    )
    st.dataframe(
        table.style.map(_paint_status, subset=["Status"]),
        width="stretch",
        hide_index=True,
    )
    st.markdown("#### Ticket detail")
    for row in rows:
        title = (
            f"{row.risk_tier} · {percent(row.model_prob)} to hit · {format_american(row.total_american_odds)} · "
            f"{row.status} · {'Real' if row.user_placed_bet else 'Paper'}"
        )
        with st.expander(title):
            st.markdown(status_pill(row.status), unsafe_allow_html=True)
            if row.rationale_summary:
                st.write(row.rationale_summary)
            if row.synergy_notes:
                st.caption(row.synergy_notes.replace("\n", " · "))
            if row.settled_at is not None:
                st.caption(f"Settled {row.settled_at.astimezone().strftime('%b %d, %I:%M %p')}.")
            if row.settled_decimal_odds and abs(row.settled_decimal_odds - row.total_decimal_odds) > 0.01:
                st.caption(
                    f"A push or void reduced the ticket to {format_american(row.settled_american_odds or row.total_american_odds)}."
                )
            leg_table = pd.DataFrame(
                [
                    {
                        "Leg": leg.selection,
                        "Game": leg.matchup,
                        "Date": leg.game_date,
                        "Status": leg.leg_status,
                        "Result": "—" if leg.actual_result is None else leg.actual_result,
                        "% it'll hit": percent(leg.model_prob),
                        "Notes": leg.settlement_notes or leg.rationale or "",
                    }
                    for leg in row.legs
                ]
            )
            st.dataframe(
                leg_table.style.map(_paint_status, subset=["Status"]),
                width="stretch",
                hide_index=True,
            )


def _paint_status(value: str) -> str:
    colors = {
        "WON": "background-color: #14532d; color: #dcfce7",
        "LOST": "background-color: #7f1d1d; color: #fee2e2",
        "PENDING": "background-color: #713f12; color: #fef3c7",
        "PUSH": "background-color: #1e3a5f; color: #dbeafe",
        "PUSHED": "background-color: #1e3a5f; color: #dbeafe",
        "VOID": "background-color: #334155; color: #e2e8f0",
    }
    return colors.get(value, "")


def render_analytics() -> None:
    st.subheader("Analytics")
    session = open_session()
    try:
        summary = performance(session)
    finally:
        session.close()
    cards = st.columns(6)
    cards[0].metric("Bankroll", money(summary["balance"]))
    cards[1].metric("Wagered", money(summary["wagered"]))
    cards[2].metric("Net", money(summary["net_profit"]))
    cards[3].metric("ROI", percent(summary["roi"]))
    cards[4].metric("Hit rate", percent(summary["hit_rate"]))
    cards[5].metric("Avg entry edge", percent(summary["avg_entry_edge"]))
    clv = percent(summary["avg_clv"]) if summary["avg_clv"] is not None else "—"
    st.caption(
        f"Closing-line value average: {clv}. "
        "CLV stays blank until a later price snapshot is stored. Entry edge is the whole ticket's hit chance times its payout multiple, minus 1, when it was built (older rows averaged per-leg edges). "
        f"Best win streak {summary['best_win_streak']}. Worst loss streak {summary['worst_loss_streak']}."
    )
    real_col, paper_col = st.columns(2)
    real_col.metric("Real-money net", money(summary["real"]["net_profit"]))
    real_col.caption(f"Hit rate {percent(summary['real']['hit_rate'])} on {summary['real']['tickets']} settled real tickets.")
    paper_col.metric("Paper net", money(summary["paper"]["net_profit"]))
    paper_col.caption(f"Hit rate {percent(summary['paper']['hit_rate'])} on {summary['paper']['tickets']} settled paper tickets.")

    tier_frame = pd.DataFrame(summary["by_tier"])
    if not tier_frame.empty:
        st.markdown("**By risk profile**")
        show = tier_frame.copy()
        for column in ("hit_rate", "roi"):
            show[column] = show[column].map(lambda value: percent(value) if pd.notna(value) else "—")
        show["wagered"] = show["wagered"].map(money)
        show["net_profit"] = show["net_profit"].map(money)
        st.dataframe(show, width="stretch", hide_index=True)

    chart_left, chart_right = st.columns(2)
    with chart_left:
        if summary["curve"]:
            curve = pd.DataFrame(summary["curve"])
            fig = go.Figure()
            fig.add_scatter(x=curve["settled_at"], y=curve["real_bankroll"], name="Real bankroll", mode="lines+markers")
            fig.add_scatter(x=curve["settled_at"], y=curve["paper_profit"], name="Paper profit", mode="lines+markers")
            fig.update_layout(title="Bankroll and paper profit")
            st.plotly_chart(_chart_layout(fig), width="stretch")
        else:
            st.info("The bankroll curve starts after the first settled ticket.")
    with chart_right:
        if summary["by_market"]:
            markets = pd.DataFrame(summary["by_market"])
            fig = go.Figure(
                go.Bar(
                    x=markets["market"],
                    y=markets["hit_rate"] * 100,
                    marker_color="#C026FF",
                    text=[f"{rate * 100:.0f}%" for rate in markets["hit_rate"]],
                    textposition="outside",
                )
            )
            fig.update_layout(title="Win rate by market", yaxis_title="Hit rate %", yaxis_range=[0, 110])
            st.plotly_chart(_chart_layout(fig), width="stretch")
        else:
            st.info("Market hit rates appear after legs are graded.")


def render_boost(board: PricedBoard, stake: float) -> None:
    st.subheader("Bet Boost grader")
    st.markdown(
        "<p class='fine'>Paste a Bet365 Bet Boost. The app matches each line to today's board and compares the model with the boosted price. This is a lean, not a guarantee.</p>",
        unsafe_allow_html=True,
    )
    price = st.text_input("Boosted price", value="+650", help="American (+650, -110) or decimal (6.50).")
    paste = st.text_area(
        "Legs, one per line",
        height=180,
        placeholder="Connor McDavid anytime goal\nOver 6.5\nEdmonton Oilers -1.5",
    )
    if st.button("Grade this boost", type="primary"):
        try:
            st.session_state.boost_grade = grade_boost(price, paste, board.legs)
        except ValueError as exc:
            st.session_state.boost_grade = None
            st.error(str(exc))
    grade = st.session_state.get("boost_grade")
    if grade is None:
        return
    pills = {
        "LEAN HIT": "good",
        "LEAN MISS": "bad",
        "TOSS-UP": "warn",
        "PARTIAL": "warn",
        "NO READ": "bad",
    }
    st.markdown(
        f'<span class="pill {pills.get(grade.lean, "")}">{grade.lean}</span>',
        unsafe_allow_html=True,
    )
    st.write(grade.summary)
    columns = st.columns(4)
    columns[0].metric("Boosted odds", format_american(grade.boosted_american))
    columns[1].metric("Price implies", percent(grade.implied_prob))
    columns[2].metric("% it'll hit", percent(grade.model_prob))
    payout = payout_amount(stake, grade.boosted_decimal)
    columns[3].metric(f"Payout on {money(stake)}", money(payout))
    if grade.board_american is not None:
        st.caption(f"Same legs at board prices multiply to {format_american(grade.board_american)}.")
    if grade.ev is not None:
        st.caption(f"Expected value at the boosted price: {percent(grade.ev)}.")
    for note in grade.notes:
        st.markdown(f"- {note}")
    if grade.unmatched:
        st.warning("Unmatched: " + " · ".join(grade.unmatched))
    if grade.matched:
        st.dataframe(
            pd.DataFrame(
                [
                    {
                        "Leg": leg.selection,
                        "Matchup": leg.matchup,
                        "Board odds": format_american(leg.american_odds),
                        "% it'll hit": percent(leg.model_prob),
                        "Lean": "Live" if leg.model_prob >= leg.implied_prob else "Fading",
                    }
                    for leg in grade.matched
                ]
            ),
            width="stretch",
            hide_index=True,
        )


def _sgp_sort_key(ticket, stake: float, sort_by: str):
    if sort_by == "% it'll hit":
        return ticket.hit_prob
    return profit_amount(stake, ticket.adjusted_decimal)


def _board_for_sport(controls: dict, sport: str) -> PricedBoard | None:
    current = st.session_state.get("board")
    if sport == controls["sport"] and current is not None and current.context.sport == sport:
        return current
    cache = st.session_state.setdefault("sport_boards", {})
    key = (controls["date"].isoformat(), controls["live"], controls["sportsbook"], sport)
    if key not in cache:
        cache[key] = load_board(
            controls["date"].isoformat(),
            prefer_live=controls["live"],
            odds_api_key=controls["odds_key"] or None,
            sportsbook=controls["sportsbook"],
            sport=sport,
        )
    return cache[key]


def render_my_parlay(controls: dict) -> None:
    st.subheader("Check my parlay")
    st.caption("Paste a bet slip, or search a name and click it. Odds can be American (+450) or decimal (1.95).")
    slip = st.text_area(
        "Paste a bet slip",
        height=160,
        placeholder="Money Line - SEA Seahawks\nEmanuel Wilson to Score a TD\nJ.K Dobbins to Score a TD\nSAME GAME PARLAY 9.75",
        key="my_parlay_slip",
    )
    sports_choice = st.radio("Legs from", ["NHL", "NFL", "NBA", "All three"], horizontal=True, key="my_parlay_sports")
    chosen = ["NHL", "NFL", "NBA"] if sports_choice == "All three" else [sports_choice]
    with st.spinner("Loading those sports..."):
        boards = {sport: _board_for_sport(controls, sport) for sport in chosen}
    choices = {}
    all_legs = []
    prop_notes = []
    for sport, slate in boards.items():
        if slate is None:
            continue
        for note in slate.context.notes:
            if note.startswith("No player props"):
                prop_notes.append(f"{sport}: {note}")
        seen_ids: set[str] = set()
        for leg in [*slate.legs, *getattr(slate, "prop_legs", [])]:
            if not leg.selectable or leg.leg_id in seen_ids:
                continue
            seen_ids.add(leg.leg_id)
            all_legs.append(leg)
            label = f"{sport} · {leg.matchup} — {leg.selection}"
            if leg.bookmaker not in {controls["sportsbook"], "synthetic-bet365", "typed"}:
                label = f"{label} · {book_label(leg.bookmaker)}"
            choices[label] = leg
    if prop_notes:
        st.info(" ".join(prop_notes))
    if "my_parlay_cart" not in st.session_state:
        st.session_state.my_parlay_cart = []
    custom = st.session_state.setdefault("my_custom_legs", {})
    query = st.text_input("Search a player or team", placeholder="Wilson, Dobbins, Seahawks, Oilers")
    needle = query.strip().lower()
    if len(needle) < 2:
        st.caption("Type at least 2 letters. Click a match and it is added. Search looks at every book.")
        matches = []
    else:
        matches = [label for label in choices if all(part in label.lower() for part in needle.split())]
        st.caption(f"{len(matches)} match{'es' if len(matches) != 1 else ''} on every book.")
    if len(needle) >= 2 and not matches:
        st.caption("Not on the odds feed. Look up his NFL, NBA, or NHL game log. Odds are optional and only change the payout.")
        manual_price = st.text_input("Odds for this leg, if you have them", placeholder="+140 or 2.40", key="my_parlay_manual_price")
        if st.button("Look up stats and add"):
            name = query.strip()
            if sports_choice == "NFL":
                attempts = [f"{name} to score a TD"]
            elif sports_choice == "NHL":
                attempts = [f"{name} to score"]
            elif sports_choice == "NBA":
                attempts = [name if any(word in name.lower() for word in ("over", "under", "point", "rebound", "assist")) else f"{name} points"]
            else:
                attempts = [f"{name} to score a TD", f"{name} to score"]
            typed = None
            with st.spinner("Looking up stats..."):
                for attempt in attempts:
                    typed = describe_player_leg(attempt)
                    if typed is not None:
                        break
            if typed is None and manual_price.strip():
                try:
                    typed = manual_leg(query.strip(), manual_price)
                except ValueError as exc:
                    st.error(str(exc))
                    typed = None
            elif typed is not None and manual_price.strip():
                try:
                    american, decimal_odds = parse_boost_price(manual_price)
                except ValueError as exc:
                    st.error(str(exc))
                else:
                    typed = typed.model_copy(
                        update={
                            "american_odds": american,
                            "decimal_odds": decimal_odds,
                            "implied_prob": typed.model_prob,
                            "bookmaker": "typed",
                            "ev": expected_value(typed.model_prob, decimal_odds),
                        }
                    )
            if typed is None:
                st.error("Could not find that player in the NFL, NBA, or NHL stats.")
            else:
                typed_label = f"Stats · {typed.selection}"
                custom[typed_label] = typed
                if typed_label not in st.session_state.my_parlay_cart:
                    st.session_state.my_parlay_cart.append(typed_label)
                st.rerun()
    if matches:
        pick = st.selectbox(
            "Click a match to add it",
            ["Choose a leg", *matches[:25]],
            key=f"my_parlay_pick_{needle}",
        )
        if pick != "Choose a leg" and pick not in st.session_state.my_parlay_cart:
            st.session_state.my_parlay_cart.append(pick)
    cart = [label for label in st.session_state.my_parlay_cart if label in choices or label in custom]
    st.session_state.my_parlay_cart = cart
    if cart:
        st.caption("On this parlay: " + " · ".join(cart))
        drop = st.multiselect("Remove legs", cart, key="my_parlay_remove")
        if drop and st.button("Remove selected"):
            for label in drop:
                custom.pop(label, None)
            st.session_state.my_parlay_cart = [label for label in cart if label not in drop]
            st.rerun()
    extra = st.text_area("Paste only if it is not in the search", height=80, placeholder="Optional")
    odds_text = st.text_input("Odds you were given", placeholder="+450 or 5.50")
    stake = st.number_input("Stake", min_value=1.0, value=float(controls["stake"] or 25), step=1.0, key="my_parlay_stake")
    if slip.strip() and st.button("Add this bet slip", type="primary"):
        groups = split_bet_slip(slip)
        results = []
        missed_lines: list[str] = []
        used: set[str] = set()
        for group in groups:
            matched = []
            missed = []
            sports_for = []
            for line in group["lines"]:
                if is_context_line(line):
                    continue
                board_leg = match_slip_line(line, all_legs, used)
                stat_leg = describe_player_leg(line) if is_player_line(line) else None
                if stat_leg is not None and board_leg is not None:
                    leg = use_book_price(stat_leg, board_leg)
                else:
                    leg = stat_leg or board_leg
                if leg is None:
                    missed.append(line)
                    missed_lines.append(line)
                    continue
                used.add(board_leg.leg_id if board_leg is not None else leg.leg_id)
                matched.append(leg)
                label = next((name for name, item in choices.items() if item.leg_id == leg.leg_id), "")
                if not label:
                    label = f"Stats · {leg.selection}"
                if stat_leg is not None:
                    custom[label] = leg
                sport_name = label.split(" · ", 1)[0] if label else ""
                if sport_name == "Stats" and leg.tags:
                    sport_name = leg.tags[0]
                sports_for.append(sport_name)
                if label not in st.session_state.my_parlay_cart:
                    st.session_state.my_parlay_cart.append(label)
            if not matched:
                results.append({"missed": missed or group["lines"]})
                continue
            try:
                if group["odds"]:
                    american, decimal_odds = parse_boost_price(group["odds"])
                else:
                    decimal_odds = combine_decimal([leg.decimal_odds for leg in matched])
                    american = decimal_to_american(decimal_odds)
            except ValueError:
                decimal_odds = combine_decimal([leg.decimal_odds for leg in matched])
                american = decimal_to_american(decimal_odds)
            hit = 1.0
            for leg in matched:
                hit *= leg.model_prob
            _score, notes = synergy(matched)
            if ticket_conflicts(matched):
                notes = ["These legs conflict and should not be on one ticket.", *notes]
            if any(leg.bookmaker == "stats" for leg in matched):
                notes = [
                    "Player chances are how often that stat happened in their logged games. The odds on the slip are the payout.",
                    *notes,
                ]
            results.append(
                {
                    "legs": matched,
                    "sports": sports_for,
                    "unmatched": missed,
                    "american": american,
                    "decimal": decimal_odds,
                    "hit": hit,
                    "notes": notes,
                    "board_decimal": combine_decimal([leg.decimal_odds for leg in matched]),
                    "ev": expected_value(hit, decimal_odds),
                    "stake": float(stake),
                }
            )
        st.session_state.my_slips = results
        st.session_state.my_slip_missing = list(dict.fromkeys(missed_lines))
        track("check_parlay", sports_choice, f"bet slip, {len(results)} ticket(s)")
    missing = st.session_state.get("my_slip_missing") or []
    if missing:
        st.caption("These names were not in the stats feed. Type each leg's odds if you still want them included. The parlay price, like 9.75, stays in Odds you were given.")
        typed_rows = []
        for line in missing:
            typed_rows.append((line, st.text_input(f"Odds for {line}", placeholder="+140 or 2.40", key=f"slip_odds_{line}")))
        if st.button("Add typed odds"):
            kept = []
            for line, price in typed_rows:
                if not str(price).strip():
                    kept.append(line)
                    continue
                try:
                    typed = manual_leg(line, price)
                except ValueError as exc:
                    st.error(f"{line}: {exc}")
                    kept.append(line)
                    continue
                typed_label = f"Typed · {typed.selection}"
                custom[typed_label] = typed
                if typed_label not in st.session_state.my_parlay_cart:
                    st.session_state.my_parlay_cart.append(typed_label)
            st.session_state.my_slip_missing = kept
            st.rerun()
    if st.button("Check all stats", type="primary"):
        selected = []
        for label in st.session_state.my_parlay_cart:
            if label in custom:
                sport_name = label.split(" · ", 1)[0]
                picked = custom[label]
                if sport_name == "Stats" and picked.tags:
                    sport_name = picked.tags[0]
                selected.append((sport_name, picked))
            elif label in choices:
                selected.append((label.split(" · ", 1)[0], choices[label]))
        unmatched: list[str] = []
        if extra.strip():
            try:
                pasted = grade_boost(odds_text or "+100", extra, all_legs)
            except ValueError as exc:
                st.error(str(exc))
                pasted = None
            if pasted is not None:
                selected.extend(("Pasted", leg) for leg in pasted.matched)
                unmatched = pasted.unmatched
        seen: set[str] = set()
        legs = []
        sports_for_legs = []
        for sport_name, leg in selected:
            if leg.leg_id in seen:
                continue
            seen.add(leg.leg_id)
            legs.append(leg)
            sports_for_legs.append(sport_name)
        if not legs:
            st.error("Add at least one leg.")
        else:
            try:
                if odds_text.strip():
                    american, decimal_odds = parse_boost_price(odds_text)
                else:
                    decimal_odds = combine_decimal([leg.decimal_odds for leg in legs])
                    american = decimal_to_american(decimal_odds)
            except ValueError as exc:
                st.error(str(exc))
            else:
                hit = 1.0
                for leg in legs:
                    hit *= leg.model_prob
                _score, notes = synergy(legs)
                if ticket_conflicts(legs):
                    notes = ["These legs conflict and should not be on one ticket.", *notes]
                board_decimal = combine_decimal([leg.decimal_odds for leg in legs])
                st.session_state.my_parlay = {
                    "legs": legs,
                    "sports": sports_for_legs,
                    "unmatched": unmatched,
                    "american": american,
                    "decimal": decimal_odds,
                    "hit": hit,
                    "notes": notes,
                    "board_decimal": board_decimal,
                    "ev": expected_value(hit, decimal_odds),
                    "stake": float(stake),
                }
                track("check_parlay", sports_choice, f"{len(legs)} legs at {odds_text or 'board price'}")
    for slip_index, slip_result in enumerate(st.session_state.get("my_slips") or [], start=1):
        if slip_result.get("legs"):
            st.markdown(f"### Bet slip {slip_index}")
            _show_checked_parlay(slip_result)
        elif slip_result.get("missed"):
            st.warning("Could not match: " + " · ".join(slip_result["missed"]))
    saved = st.session_state.get("my_parlay")
    if not saved:
        return
    _show_checked_parlay(saved)


def _show_checked_parlay(saved: dict) -> None:
    legs = saved["legs"]
    sports_for_legs = saved.get("sports") or [""] * len(legs)
    stake_used = float(saved["stake"])
    payout = payout_amount(stake_used, saved["decimal"])
    columns = st.columns(4)
    columns[0].metric("% it'll hit", percent(saved["hit"]))
    columns[1].metric("Your odds", f"{format_american(saved['american'])}  /  {saved['decimal']:.2f}")
    columns[2].metric("Payout", money(payout))
    columns[3].metric("Profit", money(profit_amount(stake_used, saved["decimal"])))
    pieces = " × ".join(percent(leg.model_prob) for leg in legs)
    st.caption(f"Each leg: {pieces}. Together: {percent(saved['hit'])}.")
    st.caption(
        f"Board price for these legs is {format_american(decimal_to_american(saved['board_decimal']))}. "
        f"Expected value at your odds: {percent(saved['ev'])}."
    )
    for note in saved["notes"]:
        st.caption(note)
    if saved["unmatched"]:
        st.warning("Unmatched: " + " · ".join(saved["unmatched"]))
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Sport": sports_for_legs[index] if index < len(sports_for_legs) else "",
                    "Leg": leg.selection,
                    "Matchup": leg.matchup,
                    "Book": book_label(leg.bookmaker),
                    "Market": leg.market_type,
                    "Line": "—" if leg.line is None else leg.line,
                    "Book odds": format_american(leg.american_odds),
                    "% it'll hit": percent(leg.model_prob),
                    "Fair %": percent(leg.fair_prob),
                    "EV": percent(leg.ev),
                    "Stats": leg.rationale,
                }
                for index, leg in enumerate(legs)
            ]
        ),
        width="stretch",
        hide_index=True,
    )


def render_sgp(board: PricedBoard, stake_default: float) -> None:
    st.subheader("Same game parlay")
    st.caption("One matchup. The stake at the top is applied to every ticket.")
    stake = st.number_input(
        "Stake for every same-game ticket",
        min_value=1.0,
        value=float(stake_default or 25),
        step=1.0,
        key="sgp_stake",
    )
    if not board.context.games:
        st.warning(f"No {board.context.sport} games on this date.")
        return
    labels = {f"{game.away.abbrev} @ {game.home.abbrev}": game.game_id for game in board.context.games}
    matchup = st.selectbox("Matchup", list(labels), key=f"sgp-matchup-{board.context.sport}")
    game_id = labels[matchup]
    game_legs = [leg for leg in board.legs if leg.game_id == game_id and leg.selectable]
    min_legs, max_legs = st.columns(2)
    least = min_legs.number_input("Minimum legs", min_value=2, max_value=6, value=2)
    most = max_legs.number_input("Maximum legs", min_value=2, max_value=6, value=3)
    side = st.selectbox("Prop side", ["ANY", "OVER_ONLY", "UNDER_ONLY"])
    categories = st.multiselect(
        "Markets",
        ["MONEYLINE", "SPREAD", "TOTAL", "PLAYER_PROP"],
        default=["MONEYLINE", "SPREAD", "TOTAL", "PLAYER_PROP"],
    )
    anchors = st.multiselect("Must-have legs", [leg.selection for leg in game_legs])
    if st.button("Build same-game tickets", type="primary"):
        wanted = {leg.leg_id for leg in game_legs if leg.selection in anchors}
        track("sgp", board.context.sport, f"{matchup} {int(least)}-{int(most)} legs")
        st.session_state.sgp_tickets = generate_valid_parlays(
            game_legs,
            ParlayFilter(
                min_legs=int(least),
                max_legs=max(int(least), int(most)),
                allowed_categories=set(categories),
                include_leg_ids=wanted,
                prop_side=side,
            ),
        )
    tickets = list(st.session_state.get("sgp_tickets") or [])
    if not tickets:
        st.caption("Pick a game and build. Opposite sides, alternate lines, and a moneyline stacked with that team's spread are left out.")
        return
    sort_col, order_col = st.columns(2)
    sort_by = sort_col.selectbox("Sort by", ["Amount to win", "% it'll hit"])
    order = order_col.selectbox("Order", ["Descending", "Ascending"])
    tickets.sort(key=lambda ticket: _sgp_sort_key(ticket, float(stake), sort_by), reverse=order == "Descending")
    if tickets[0].truncated:
        st.warning("The search stopped early and is showing the tickets found so far.")
    for index, ticket in enumerate(tickets, start=1):
        payout = payout_amount(float(stake), ticket.adjusted_decimal)
        profit = profit_amount(float(stake), ticket.adjusted_decimal)
        with st.container(border=True):
            st.markdown(f"### SGP {index}")
            left, right, third, fourth = st.columns(4)
            left.metric("% it'll hit", percent(ticket.hit_prob))
            right.metric("You win", money(profit))
            third.metric("Payout", money(payout))
            fourth.metric("SGP odds", format_american(ticket.adjusted_american))
            st.caption(f"On a {cash(float(stake))} stake. Raw price before the same-game adjustment: {format_american(ticket.raw_american)}.")
            st.write(", ".join(leg.label for leg in ticket.legs))
            for note in ticket.notes:
                st.caption(note)


def render_target(controls: dict) -> None:
    st.subheader("Hit a number")
    st.caption("Find tickets whose payout matches what you want to bet, or what you want to win.")
    mode = st.radio("Amount means", ["I want to bet", "I want to win"], horizontal=True)
    amount = st.number_input("Dollars", min_value=1.0, value=100.0, step=5.0)
    stake = st.number_input("Stake used to size a win target", min_value=1.0, value=float(controls["stake"] or 25), step=1.0)
    sports = st.radio("Sports", ["NHL", "NFL", "NBA", "All three"], horizontal=True)
    if st.button("Find tickets", type="primary"):
        chosen = ["NHL", "NFL", "NBA"] if sports == "All three" else [sports]
        track("target", sports, f"{mode} ${amount:.0f}")
        pools = {}
        with st.spinner("Pricing the selected sports..."):
            for sport in chosen:
                if sport == controls["sport"]:
                    slate = st.session_state.get("board")
                else:
                    cache = st.session_state.setdefault("sport_boards", {})
                    key = (controls["date"].isoformat(), controls["live"], controls["sportsbook"], sport)
                    if key not in cache:
                        cache[key] = load_board(
                            controls["date"].isoformat(),
                            prefer_live=controls["live"],
                            odds_api_key=controls["odds_key"] or None,
                            sportsbook=controls["sportsbook"],
                            sport=sport,
                        )
                    slate = cache[key]
                if slate is not None:
                    pools[sport] = slate.legs
        stake_used = float(amount) if mode == "I want to bet" else float(stake)
        win_target = None if mode == "I want to bet" else float(amount)
        st.session_state.target_tickets = hunt_parlays(pools, stake=stake_used, win_target=win_target)
        st.session_state.target_mode = mode
    tickets = st.session_state.get("target_tickets") or []
    if not tickets:
        st.caption("All three builds one ticket that can mix NHL, NFL, and NBA legs.")
        return
    for index, ticket in enumerate(tickets, start=1):
        with st.container(border=True):
            st.markdown(f"### Ticket {index} · {', '.join(dict.fromkeys(ticket.sports))}")
            columns = st.columns(5)
            columns[0].metric("% it'll hit", percent(ticket.hit_prob))
            columns[1].metric("Odds", format_american(ticket.american_odds))
            columns[2].metric("Stake", money(ticket.stake))
            columns[3].metric("Payout", money(ticket.payout))
            columns[4].metric("Profit", money(ticket.profit))
            st.caption(f"Expected value {percent(ticket.ev)}.")
            for leg in ticket.legs:
                st.write(f"- {leg.selection} ({leg.matchup}) · {percent(leg.model_prob)} it'll hit")


def session_ips() -> list[str]:
    found: list[str] = []
    try:
        context_ip = str(getattr(st.context, "ip_address", "") or "").strip()
    except Exception:
        context_ip = ""
    if context_ip:
        found.append(context_ip)
    public_ip = lookup_public_ip()
    if public_ip and public_ip not in found:
        found.append(public_ip)
    return found


def track(action: str, sport: str, summary: str) -> None:
    account = st.session_state.get("account") or {}
    record_event(
        action,
        sport,
        summary,
        username=account.get("username", ""),
        name=account.get("name", ""),
        ips=", ".join(account.get("ips") or []),
    )


def render_login() -> None:
    st.markdown(
        """
        <div class="hero">
            <h1>DARK<span>NODE</span></h1>
            <p>Create an account to use the app. The owner keeps a log of your name, username, and sign-in IP.</p>
            <p class="credit">Created and Owned by Harvir Vineypal</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    saved_login = load_remembered()
    if saved_login and not st.session_state.get("skip_auto_login"):
        account = authenticate(saved_login["username"], saved_login["password"])
        if account is not None:
            ips = remember_ips(account["username"], session_ips())
            account["ips"] = ips
            st.session_state.account = account
            record_event("sign_in", "", "Signed in", username=account["username"], name=account["name"], ips=", ".join(ips))
            st.rerun()
    if "login_user" not in st.session_state and saved_login:
        st.session_state.login_user = saved_login["username"]
        st.session_state.login_pass = saved_login["password"]
    sign_in, create = st.tabs(["Sign in", "Create account"])
    with sign_in:
        username = st.text_input("Username", key="login_user")
        password = st.text_input("Password", type="password", key="login_pass")
        remember = st.checkbox("Remember me", value=bool(saved_login))
        if st.button("Sign in", type="primary"):
            account = authenticate(username, password)
            if account is None:
                st.error("That username and password do not match.")
            else:
                if remember:
                    save_remembered(username, password)
                else:
                    clear_remembered()
                ips = remember_ips(account["username"], session_ips())
                account["ips"] = ips
                st.session_state.account = account
                st.session_state.skip_auto_login = False
                record_event("sign_in", "", "Signed in", username=account["username"], name=account["name"], ips=", ".join(ips))
                st.rerun()
    with create:
        name = st.text_input("Full name", key="create_name")
        new_user = st.text_input("Username", key="create_user")
        new_pass = st.text_input("Password", type="password", key="create_pass")
        again = st.text_input("Repeat password", type="password", key="create_pass2")
        if st.button("Create account"):
            if new_pass != again:
                st.error("Passwords do not match.")
            else:
                error = create_account(name, new_user, new_pass)
                if error:
                    st.error(error)
                else:
                    st.success("Account created. Sign in with that username and password.")


def render_owner_log() -> None:
    st.subheader("Owner log")
    st.caption("Names, usernames, sign-in IPs, and actions. Passwords and API keys are not stored here.")
    if not os.getenv("PUCKEDGE_ADMIN_PASSWORD", "").strip():
        st.info("No owner password is configured on this copy. Friends cannot open this log, and this shared app does not contain the owner's keys.")
        return
    if not st.session_state.get("owner_unlocked"):
        typed = st.text_input("Owner password", type="password")
        if st.button("Unlock log"):
            if password_matches(typed):
                st.session_state.owner_unlocked = True
                st.rerun()
            else:
                st.error("Wrong password.")
        return
    st.markdown("**Accounts**")
    accounts = list_accounts()
    if accounts:
        st.dataframe(pd.DataFrame(accounts), width="stretch", hide_index=True)
    else:
        st.caption("No accounts on this computer yet.")
    st.markdown("**Paid access**")
    st.caption("Times are local, like 2026-11-01 09:00. Save, then push access.json to GitHub so other computers pick it up.")
    rules = load_local_access()
    for account in accounts:
        username = account["username"]
        current = rules.get(username) or {}
        with st.expander(f"Access for {account['name']} ({username})"):
            approved = st.checkbox("Approved", value=bool(current.get("approved")), key=f"ok-{username}")
            start = st.text_input("Access starts", value=str(current.get("start") or ""), key=f"start-{username}")
            end = st.text_input("Access ends", value=str(current.get("end") or ""), key=f"end-{username}")
            if st.button("Save access", key=f"save-{username}"):
                rules[username] = {"approved": approved, "start": start.strip(), "end": end.strip()}
                save_access(rules)
                st.success(f"Saved {username}. Push access.json to GitHub when you want that copy to go live.")
    st.markdown("**Activity**")
    events = read_events()
    if events:
        st.dataframe(pd.DataFrame(events), width="stretch", hide_index=True)
    else:
        st.caption("No activity yet.")


def _slip() -> list[dict]:
    return st.session_state.setdefault("slip", [])


def _values_for(market, bundle: dict) -> list[float] | None:
    gamelog = series_from_gamelog(bundle.get("gamelog") or {}, market.stat_keys)
    if gamelog is not None:
        return gamelog
    overview = series_from_overview(bundle.get("overview") or {}, market.stat_keys)
    if overview is not None:
        return overview
    if market.nhl_key and bundle.get("nhl"):
        return series_from_nhl(bundle["nhl"], market.nhl_key)
    return None


def _priced(probability: float) -> tuple[int, float]:
    priced = min(max(probability, 0.03), 0.99)
    return implied_to_american(priced), 1.0 / priced


def _quote(entity: dict, row, board: PricedBoard | None, preferred: str, market, values=None, bundle=None) -> dict | None:
    if row.probability is None:
        return None
    pool = []
    if board is not None:
        pool = [*board.legs, *getattr(board, "prop_legs", [])]
    chosen, best = matching_books(
        pool,
        name=entity["name"],
        board_market=market.board_market,
        side=row.side,
        line=row.line,
        preferred=preferred,
    )
    matchup = str((bundle or {}).get("matchup") or "")
    if chosen is not None and chosen.matchup:
        matchup = chosen.matchup
    chance = row.probability
    opponent = ""
    context = tonight(entity["sport"], str(entity.get("team") or ""), matchup, market.key)
    if context is not None and values:
        adjusted = adjusted_probability(market, row.side, row.line, values, context["factor"])
        if adjusted is not None:
            chance = adjusted
            opponent = context["note"]
    american, decimal = _priced(chance)
    book = "from stats"
    game_id = entity.get("espn_id") or entity["name"]
    if chosen is not None:
        american, decimal, book = chosen.american_odds, chosen.decimal_odds, chosen.bookmaker
        game_id = chosen.game_id
    best_note = ""
    if best is not None and best.bookmaker != book:
        best_note = f"Best posted price is {book_label(best.bookmaker)} {format_american(best.american_odds)}."
    return {
        "id": f"{entity.get('espn_id')}|{row.key}|{book}",
        "sport": entity["sport"],
        "player": entity["name"],
        "selection": f"{entity['name']} {row.label}",
        "probability": chance,
        "season_hit": row.probability,
        "sample": row.sample,
        "l5": row.l5_text,
        "season_avg": row.season_avg,
        "opponent": opponent,
        "american": american,
        "decimal": decimal,
        "book": book,
        "best_note": best_note,
        "matchup": matchup,
        "game_id": str(game_id),
        "rationale": f"{row.sample} Last 5: {row.l5_text}. {opponent}".strip(),
        **_conflict_fields(entity["sport"], market, row, matchup, chosen),
    }


def _add_quote(quote: dict) -> None:
    slip = _slip()
    if any(item["id"] == quote["id"] for item in slip):
        return
    problem = slip_conflict(quote, slip)
    if problem:
        st.session_state.setdefault("slip_notices", []).append(problem)
        return
    slip.append(quote)


def _team_market_type(sport: str, market_key: str) -> str:
    if market_key == "team_ml":
        return "MONEYLINE"
    if market_key == "team_spread":
        return "PUCKLINE" if sport == "NHL" else "SPREAD"
    if market_key == "team_team_total":
        return "TEAM_TOTAL"
    return "TOTAL"


def _conflict_fields(sport: str, market, row, matchup: str, chosen) -> dict:
    """What the slip needs to spot opposite sides and Under-with-scorers stacks."""
    pick = {"anytime": "YES", "milestone": "OVER"}.get(row.side, str(row.side).upper())
    line = row.line
    if row.side == "milestone" and line is not None:
        line = float(line) - 0.5
    if row.side == "anytime":
        line = 0.5
    game_key = f"{sport}|{matchup}" if matchup else ""
    return {
        "game_key": game_key,
        "market_type": market.board_market,
        "pick": pick,
        "line": line,
        "team": "",
    }


def _team_quotes(entity: dict, market, board: PricedBoard | None) -> list[dict]:
    label = market.label
    if board is None or board.context.sport != entity["sport"]:
        return []
    quotes = []
    needle = entity["name"].casefold()
    for game in board.context.games:
        for team, side in ((game.home, "HOME"), (game.away, "AWAY")):
            if needle not in team.name.casefold() and needle != team.abbrev.casefold():
                continue
            matchup = f"{game.away.abbrev} @ {game.home.abbrev}"
            if market.key == "team_ml":
                leg = next((item for item in board.legs if item.game_id == game.game_id and item.market_type == "MONEYLINE" and item.side == side and item.selectable), None)
                quotes.append(_team_quote(entity, leg, f"{team.name} moneyline", matchup, board, team.abbrev, game, market.key))
            elif market.key == "team_spread":
                kind = "PUCKLINE" if entity["sport"] == "NHL" else "SPREAD"
                leg = next((item for item in board.legs if item.game_id == game.game_id and item.market_type == kind and item.side == side and item.selectable), None)
                quotes.append(_team_quote(entity, leg, leg.selection if leg else f"{team.name} {label}", matchup, board, team.abbrev, game, market.key))
            elif market.key == "team_total":
                overs = [item for item in board.legs if item.game_id == game.game_id and item.market_type == "TOTAL" and item.selectable]
                if not overs:
                    quotes.append(_team_quote(entity, None, f"{matchup} total", matchup, board, team.abbrev, game, market.key))
                else:
                    for leg in overs:
                        if leg.side in {"OVER", "UNDER"}:
                            quotes.append(_team_quote(entity, leg, leg.selection, matchup, board, team.abbrev, game, market.key))
            elif market.key == "team_team_total":
                legs = [item for item in board.legs if item.game_id == game.game_id and item.market_type == "TEAM_TOTAL" and item.team_abbrev == team.abbrev and item.selectable]
                if not legs:
                    quotes.append(_team_quote(entity, None, f"{team.name} team total", matchup, board, team.abbrev, game, market.key))
                else:
                    for leg in legs:
                        quotes.append(_team_quote(entity, leg, leg.selection, matchup, board, team.abbrev, game, market.key))
    return [quote for quote in quotes if quote is not None]


def _team_quote(entity, leg, label: str, matchup: str, board: PricedBoard, abbrev: str, game, market_key: str) -> dict | None:
    if leg is not None and leg.bookmaker not in {"synthetic-bet365", "stats", "typed", "from stats"}:
        probability = leg.model_prob
        american, decimal, book = leg.american_odds, leg.decimal_odds, leg.bookmaker
        sample = "team model on the slate"
    elif leg is not None:
        probability = leg.model_prob
        american, decimal = _priced(probability)
        book = "from stats"
        sample = "team model on the slate"
    elif market_key != "team_ml":
        return {
            "id": f"{entity['espn_id']}|{label}|empty",
            "sport": entity["sport"],
            "player": entity["name"],
            "selection": label,
            "probability": None,
            "sample": "no stat sample",
            "l5": "no stat sample",
            "season_avg": None,
            "american": 0,
            "decimal": 0,
            "book": "from stats",
            "best_note": "",
            "matchup": matchup,
            "game_id": game.game_id,
            "rationale": "no stat sample",
        }
    else:
        profile = board.context.teams.get(abbrev)
        opponent = game.away if game.home.abbrev == abbrev else game.home
        other = board.context.teams.get(opponent.abbrev)
        if profile is None or not profile.games_played:
            return {
                "id": f"{entity['espn_id']}|{label}|empty",
                "sport": entity["sport"],
                "player": entity["name"],
                "selection": label,
                "probability": None,
                "sample": "no stat sample",
                "l5": "no stat sample",
                "season_avg": None,
                "american": 0,
                "decimal": 0,
                "book": "from stats",
                "best_note": "",
                "matchup": matchup,
                "game_id": game.game_id,
                "rationale": "no stat sample",
            }
        if other is not None and profile.gf_per_game and other.gf_per_game:
            own = (profile.gf_per_game + other.ga_per_game) / 2
            theirs = (other.gf_per_game + profile.ga_per_game) / 2
            probability = own / (own + theirs) if own + theirs else 0.5
        else:
            probability = profile.wins / profile.games_played
        american, decimal = _priced(probability)
        book = "from stats"
        sample = f"season scoring {profile.gf_per_game:.1f} for and {profile.ga_per_game:.1f} against, not a player log"
    return {
        "id": f"{game.game_id}|{label}|{book}",
        "sport": entity["sport"],
        "player": entity["name"],
        "selection": label,
        "probability": probability,
        "sample": sample,
        "l5": "—",
        "season_avg": None,
        "american": american,
        "decimal": decimal,
        "book": book,
        "best_note": "",
        "matchup": matchup,
        "game_id": game.game_id,
        "rationale": sample,
        "game_key": f"{entity['sport']}|{matchup}",
        "market_type": _team_market_type(entity["sport"], market_key),
        "pick": leg.side if leg is not None else ("HOME" if game.home.abbrev == abbrev else "AWAY"),
        "line": leg.line if leg is not None else None,
        "team": (leg.team_abbrev if leg is not None and leg.team_abbrev else abbrev) if market_key != "team_total" else "",
    }


def _show_quote_row(quote: dict) -> None:
    left, mid, right = st.columns([3, 2, 1])
    left.markdown(f"**{quote['selection']}**")
    if quote.get("probability") is None:
        mid.caption("no stat sample")
        return
    average = "—" if quote.get("season_avg") is None else f"{quote['season_avg']:.1f}"
    mid.caption(f"Avg {average} · L5 {quote.get('l5') or '—'} · Season {quote['sample']} · {format_american(quote['american'])} {book_label(quote['book'])}")
    if quote.get("opponent"):
        mid.caption(f"vs opponent {percent(quote.get('probability'))} · {quote['opponent']}")
    if quote.get("best_note"):
        mid.caption(quote["best_note"])
    if right.button("Add", key=f"add-{quote['id']}"):
        _add_quote(quote)
        st.rerun()


def _slate_boards(controls: dict, board: PricedBoard) -> dict[str, PricedBoard]:
    found: dict[str, PricedBoard] = {}
    for sport in ("NHL", "NFL", "NBA"):
        try:
            found[sport] = board if board.context.sport == sport else _board_for_sport(controls, sport)
        except Exception:
            continue
    return found


def render_hit_rates_mode(slate: dict[str, PricedBoard], controls: dict) -> None:
    """Find mode: pick a league and a game, then show the same 100% Hit Rates card as the dialog."""
    league = st.radio("League", ["NHL", "NBA", "NFL"], horizontal=True, key="hr_league")
    board = slate.get(league)
    games = list(board.context.games) if board else []
    if not games:
        st.caption(f"No {league} games on this date.")
        return
    by_id = {game.game_id: game for game in games}
    picked = st.selectbox(
        "Game",
        list(by_id),
        format_func=lambda game_id: f"{by_id[game_id].away.abbrev} @ {by_id[game_id].home.abbrev} · {format_start(by_id[game_id].start_time_utc)}",
        key=f"hr_game_{league}",
    )
    render_hit_rates(
        board,
        by_id[picked],
        league=league,
        preferred_book=str(controls.get("sportsbook") or ""),
        add_quote=_add_quote,
        ctx="find",
        autoscan=False,
    )


def _team_logo(league: str, abbrev: str) -> str:
    """CDN URL for a known team, or an empty string (callers render a text badge instead)."""
    return team_logo_url(league, abbrev) or ""

def _team_record(slate: PricedBoard, abbrev: str) -> str:
    team = slate.context.teams.get(abbrev)
    if team is None:
        return ""
    if team.ot_losses:
        return f"{team.wins}-{team.losses}-{team.ot_losses}"
    return f"{team.wins}-{team.losses}"


def _odds_strip(slate: PricedBoard, game) -> str:
    legs = [leg for leg in slate.legs if leg.game_id == game.game_id]
    home_ml = next((leg for leg in legs if leg.market_type == "MONEYLINE" and leg.side == "HOME"), None)
    away_ml = next((leg for leg in legs if leg.market_type == "MONEYLINE" and leg.side == "AWAY"), None)
    spread = next((leg for leg in legs if leg.market_type in {"SPREAD", "PUCKLINE"} and leg.side == "HOME"), None)
    total = next((leg for leg in legs if leg.market_type == "TOTAL" and leg.side == "OVER"), None)
    spread_text = f"{spread.line:+.1f}" if spread is not None and spread.line is not None else "—"
    away_text = format_american(away_ml.american_odds) if away_ml else "—"
    home_text = format_american(home_ml.american_odds) if home_ml else "—"
    total_text = f"O/U {total.line:g}" if total is not None and total.line is not None else "—"
    return f"<span>Spread <b>{spread_text}</b></span><span>ML <b>{away_text}</b> / <b>{home_text}</b></span><span><b>{total_text}</b></span>"


def _status_line(game) -> str:
    state = game.game_state or "Scheduled"
    if game.away_score is not None and game.home_score is not None:
        return f"{state} · {game.away.abbrev} {game.away_score}  {game.home.abbrev} {game.home_score}"
    return state


def _clear_open_game() -> None:
    st.session_state.open_game = ""


@st.dialog("Matchup", width="large", on_dismiss=_clear_open_game)
def _open_matchup_dialog(league: str, status: str, game, board: PricedBoard) -> None:
    away, home = game.away.abbrev, game.home.abbrev
    title, closer = st.columns([8, 1])
    title.markdown(f"**{league}** · {status}")
    if closer.button("✕", key="close-matchup"):
        st.session_state.open_game = ""
        st.rerun()
    if st.button("Open props", key="modal-open-props"):
        st.session_state.picker_autoload = home
        st.session_state.open_game = ""
        st.rerun()
    render_hit_rates(
        board,
        game,
        league=league,
        preferred_book=str(st.session_state.get("sportsbook") or ""),
        add_quote=_add_quote,
        ctx="dialog",
        autoscan=True,
    )


def _started(game) -> bool:
    return str(game.game_state or "").upper() not in {"", "FUT", "PRE", "SCHEDULED"}


def _as_score(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _team_result(game, score, other) -> str:
    score = _as_score(score)
    other = _as_score(other)
    if not _started(game) or score is None or other is None:
        return ""
    mark = ""
    if score > other:
        mark = "<span class='sn-w'>W</span>"
    elif score < other:
        mark = "<span class='sn-l'>L</span>"
    return f"<div class='sn-result'><span>{score}</span>{mark}</div>"


def _overlay_scores(boards: dict[str, PricedBoard], slate_date: str) -> None:
    """Pull fresh scoreboards onto the cards. Odds stay cached."""
    from src.api.espn_client import ESPN_PATH, SCOREBOARD, parse_scoreboard
    from src.api.http_client import get_json
    from src.api.nhl_client import parse_schedule
    from config import NHL_WEB_BASE

    fresh: dict[str, list] = {}
    for sport in ("NBA", "NFL"):
        if sport not in boards:
            continue
        try:
            path = ESPN_PATH[sport]
            payload = get_json(SCOREBOARD.format(path=path), params={"dates": slate_date.replace("-", "")}, ttl_seconds=20)
            fresh[sport] = parse_scoreboard(payload, slate_date)
        except Exception:
            fresh[sport] = []
    if "NHL" in boards:
        try:
            payload = get_json(f"{NHL_WEB_BASE}/schedule/{slate_date}", ttl_seconds=20)
            games, _appearances = parse_schedule(payload, slate_date)
            fresh["NHL"] = games
        except Exception:
            fresh["NHL"] = []
    for sport, slate in boards.items():
        incoming = fresh.get(sport) or []
        by_id = {game.game_id: game for game in incoming}
        by_pair = {(game.away.abbrev, game.home.abbrev): game for game in incoming}
        for game in slate.context.games:
            update = by_id.get(game.game_id) or by_pair.get((game.away.abbrev, game.home.abbrev))
            if update is None:
                continue
            game.game_state = update.game_state
            game.home_score = update.home_score
            game.away_score = update.away_score


_MULTIWORD_NICKNAMES = (
    "Maple Leafs", "Blue Jackets", "Red Wings", "Golden Knights", "Trail Blazers", "Hockey Club",
)


def _nickname(name: str, abbrev: str) -> str:
    """'Patriots' from 'New England Patriots', 'Maple Leafs' from 'Toronto Maple Leafs'."""
    text = " ".join(str(name or "").split())
    if not text:
        return abbrev
    for nick in _MULTIWORD_NICKNAMES:
        if text.endswith(nick):
            return nick
    parts = text.split(" ")
    return parts[-1] if len(parts) > 1 else text


def _tick_status(game) -> str:
    state = str(game.game_state or "").upper()
    if state in {"FINAL", "OFF", "OFFICIAL"}:
        return "<span>Final</span>"
    if state in {"LIVE", "CRIT", "IN"}:
        return "<span class='live-dot'></span><span>Live</span>"
    return f"<span>{html.escape(format_start(game.start_time_utc))}</span>"


def _tick_logo(league: str, abbrev: str) -> str:
    return team_logo_html(league, abbrev, size=28, css_class="tick-logo")


def _tick_row(slate: PricedBoard, league: str, game, side, score, other) -> str:
    abbrev = side.abbrev
    record = _team_record(slate, abbrev)
    record_html = f"<small>{html.escape(record)}</small>" if record else ""
    return (
        "<div class='tick-row'>"
        f"<span class='tick-left'>{_tick_logo(league, abbrev)}"
        f"<span class='tick-name'><b>{html.escape(abbrev)} · {html.escape(_nickname(side.name, abbrev))}</b>{record_html}</span></span>"
        f"{_team_result(game, score, other)}</div>"
    )


def _tick_chip(league: str, game, slate: PricedBoard) -> str:
    live = str(game.game_state or "").upper() in {"LIVE", "CRIT", "IN"}
    return (
        "<div class='tick'>"
        f"<div class='tick-status{' live' if live else ''}'>{_tick_status(game)}</div>"
        + _tick_row(slate, league, game, game.away, game.away_score, game.home_score)
        + _tick_row(slate, league, game, game.home, game.home_score, game.away_score)
        + "</div>"
    )


def render_scoreboard(boards: dict[str, PricedBoard], slate_date: str) -> None:
    _overlay_scores(boards, slate_date)
    order = ["NHL", "NBA", "NFL"]
    if "sport_idx" not in st.session_state:
        st.session_state.sport_idx = 0
    counts = {sport: len(boards[sport].context.games) if boards.get(sport) else 0 for sport in order}
    current = order[st.session_state.sport_idx % 3]
    # Full-width segmented control: every league name is readable, none is clipped.
    choice = st.segmented_control(
        "League",
        order,
        selection_mode="single",
        default=current,
        format_func=lambda sport: f"{sport} ({counts[sport]})",
        key="league-seg",
        label_visibility="collapsed",
    )
    if choice and choice != current:
        st.session_state.sport_idx = order.index(choice)
        current = choice
    dots = "".join(f"<span class='sn-dot {'active' if sport == current else ''}'></span>" for sport in order)
    st.markdown(
        f"<div style='text-align:center'><span class='sn-league-badge sn-badge-{current.lower()}'>{current} · {counts[current]} games</span><div>{dots}</div></div>",
        unsafe_allow_html=True,
    )
    slate = boards.get(current)
    games = list(slate.context.games) if slate else []
    if not games:
        st.caption(f"No {current} games on this date.")
        return
    if st.query_params.get("game"):
        st.query_params.clear()
    opened = None
    with st.container(horizontal=True, wrap=False, gap="small", vertical_alignment="top", key="score-row"):
        for game in games:
            with st.container():
                st.markdown(f"<div class='tick-strip'>{_tick_chip(current, game, slate)}</div>", unsafe_allow_html=True)
                token = f"{current}:{game.game_id}"
                if st.button("Details", key=f"card-{token}", width="stretch", help=f"{game.away.abbrev} @ {game.home.abbrev}"):
                    st.session_state.open_game = token
                    st.rerun()
                if st.session_state.get("open_game") == token:
                    opened = game
    if opened is not None:
        _open_matchup_dialog(current, _status_line(opened), opened, slate)


if os.getenv("PUCKEDGE_DISABLE_SCHEDULER") != "1":
    render_scoreboard = st.fragment(run_every="30s")(render_scoreboard)


def render_picker(controls: dict, board: PricedBoard) -> None:
    pending = str(st.session_state.get("picker_autoload") or "")
    if pending:
        st.session_state.omni_query = pending
    query = st.text_input("Search a player or team", placeholder="Wilson, McDavid, Jokic, Oilers", key="omni_query")
    needle = query.strip()
    if pending and needle:
        found = search_entities(needle)
        st.session_state.picker_autoload = ""
        if found:
            st.session_state.picked = found[0]
    if len(needle) >= 2:
        found = search_entities(needle)
        if not found:
            st.caption("No NHL, NBA, or NFL match.")
        else:
            labels = ["Choose a match"] + [f"{row['sport']} · {row['team'] or row['kind'].title()} · {row['name']}" for row in found]
            choice = st.selectbox("Matches", labels, key=f"omni-open-{needle}", label_visibility="collapsed")
            if choice != "Choose":
                st.session_state.picked = found[labels.index(choice) - 1]
    entity = st.session_state.get("picked")
    if not entity:
        return
    st.markdown(f"### {entity['sport']} · {entity['name']}")
    if entity["kind"] == "team":
        bundle = {"position": ""}
        group = "team"
    else:
        bundles = st.session_state.setdefault("player_bundles", {})
        if entity["espn_id"] not in bundles:
            with st.spinner("Loading the game log..."):
                bundles[entity["espn_id"]] = load_player(entity)
        bundle = bundles[entity["espn_id"]]
        position = str(bundle.get("position") or "").upper()
        group = "goalie" if entity["sport"] == "NHL" and position in {"G", "GOALIE"} else "skater" if entity["sport"] == "NHL" else "player"
    markets = markets_for(entity["sport"], group)
    if not markets:
        st.caption("No markets for this selection.")
        return
    keys = {market.key for market in markets}
    if st.session_state.get("slip_market_for") != entity["espn_id"] or st.session_state.get("slip_market") not in keys:
        st.session_state.slip_market = markets[0].key
        st.session_state.slip_market_for = entity["espn_id"]
    columns = st.columns(4)
    for index, market in enumerate(markets):
        kind = "primary" if market.key == st.session_state.slip_market else "secondary"
        if columns[index % 4].button(market.label, key=f"market-{entity['espn_id']}-{market.key}", type=kind):
            st.session_state.slip_market = market.key
            st.rerun()
    market = next(item for item in markets if item.key == st.session_state.slip_market)
    st.caption(market.label)
    try:
        sport_board = board if board.context.sport == entity["sport"] else _board_for_sport(controls, entity["sport"])
    except Exception:
        sport_board = None
    if entity["kind"] == "team":
        quotes = _team_quotes(entity, market, sport_board)
        if not quotes:
            st.caption("no stat sample")
            return
        for quote in quotes:
            _show_quote_row(quote)
        return
    values = _values_for(market, bundle)
    custom_text = st.text_input(
        f"Custom {market.label} line",
        placeholder="5+ or 2.5+ or under 1.5",
        key=f"custom-line-{entity['espn_id']}-{market.key}",
    )
    parsed = parse_custom_line(custom_text, market.label)
    if custom_text.strip() and parsed is None:
        st.caption("Type 5+, 2.5+, or under 1.5.")
    elif parsed is not None:
        side, line, label = parsed
        custom_row = rate_row(market, side, line, values, label)
        if custom_row.probability is None:
            st.caption(f"{label}: no stat sample")
        else:
            custom_quote = _quote(entity, custom_row, sport_board, controls["sportsbook"], market, values, bundle)
            if custom_quote is not None:
                if bundle.get("matchup") and not custom_quote["matchup"]:
                    custom_quote["matchup"] = bundle["matchup"]
                _show_quote_row(custom_quote)
    for row in rows_for_market(market, values):
        if row.probability is None:
            st.caption(f"{row.label}: no stat sample")
            continue
        quote = _quote(entity, row, sport_board, controls["sportsbook"], market, values, bundle)
        if quote is None:
            st.caption(f"{row.label}: no stat sample")
            continue
        if bundle.get("matchup") and not quote["matchup"]:
            quote["matchup"] = bundle["matchup"]
        _show_quote_row(quote)


def _slip_ticket(stake: float, balance: float) -> BuiltParlay | None:
    slip = [leg for leg in _slip() if leg.get("probability") is not None]
    if not slip:
        return None
    price_text = str(st.session_state.get("slip_price") or "").strip()
    if price_text:
        american, decimal = parse_boost_price(price_text)
    else:
        decimal = combine_decimal([float(leg["decimal"]) for leg in slip])
        american = decimal_to_american(decimal)
    hit = slip_chance(slip)
    kelly = kelly_fraction(decimal, hit, QUARTER_KELLY)
    recommended = balance * kelly
    note = same_game_note(slip)
    notes = [note] if note else []
    pieces = " × ".join(percent(leg["probability"]) for leg in slip)
    notes.append(f"Each leg: {pieces}. Together: {percent(hit)}.")
    legs = []
    for leg in slip:
        legs.append(
            MarketLeg(
                leg_id=str(leg["id"])[:40],
                game_id=str(leg.get("game_id") or leg["id"]),
                matchup=str(leg.get("matchup") or leg["sport"]),
                game_date="",
                start_time_utc="",
                market_type="CUSTOM",
                market_key=str(leg["id"]),
                target_entity=str(leg["player"]),
                selection=str(leg["selection"]),
                side="YES",
                american_odds=int(leg["american"]),
                decimal_odds=float(leg["decimal"]),
                implied_prob=float(leg["probability"]),
                fair_prob=float(leg["probability"]),
                model_prob=float(leg["probability"]),
                ev=expected_value(float(leg["probability"]), float(leg["decimal"])),
                bookmaker=str(leg["book"]),
                rationale=str(leg.get("rationale") or leg.get("sample") or ""),
                sport=str(leg["sport"]).upper() if str(leg.get("sport") or "").upper() in {"NHL", "NBA", "NFL"} else None,
            )
        )
    return BuiltParlay(
        risk_tier="Custom",
        legs=legs,
        american_odds=american,
        decimal_odds=decimal,
        model_prob=hit,
        ev=expected_value(hit, decimal),
        synergy_score=0,
        synergy_notes=notes,
        rationale_summary="Custom slip. Hit chances are from game logs.",
        within_odds_band=True,
        recommended_stake=recommended,
        guideline_low=0.0,
        guideline_high=0.0,
        kelly_fraction=kelly,
        exposure_pct=exposure_pct(stake, balance),
        exposure_warning=sizing_warning("Balanced", stake, balance, recommended),
        stake=stake,
        potential_payout=payout_amount(stake, decimal),
        net_profit_if_win=profit_amount(stake, decimal),
        entry_edge=expected_value(hit, decimal),
    )


def render_slip(stake: float, balance: float) -> None:
    st.markdown("**Ticket**")
    if "target_stake" not in st.session_state:
        st.session_state.target_stake = float(stake)
    stake = st.number_input("Wager", min_value=0.0, step=5.0, key="target_stake", help=TIPS["Wager"])
    slip = _slip()
    for notice in st.session_state.pop("slip_notices", []):
        st.warning(notice)
    if slip and st.button("Clear"):
        st.session_state.slip = []
        st.rerun()
    if not slip:
        st.caption("Search a player, pick a stat, and add it here.")
        return
    kept = []
    for index, leg in enumerate(slip):
        # One stacked card per leg. The ticket column is only about a third of the page,
        # so a six-column table would squeeze every label into a vertical stick.
        with st.container(border=True):
            name_col, close_col = st.columns([6, 1], vertical_alignment="center")
            with name_col:
                bubble(str(leg.get("player") or leg["selection"]), f"slip-player-{index}")
                st.caption(leg["selection"])
            remove = close_col.button("✕", key=f"slip-remove-{index}", help="Remove this leg")
            season_hit = leg.get("season_hit", leg.get("probability"))
            versus = percent(leg.get("probability")) if leg.get("opponent") else "—"
            st.markdown(
                "<div class='ticket-stats'>"
                + _stat("Season", percent(season_hit))
                + _stat("Last 5", str(leg.get("l5") or "—"))
                + _stat("vs opponent", versus)
                + _stat("Odds", f"{format_american(leg['american'])} / {float(leg['decimal']):.2f}")
                + "</div>",
                unsafe_allow_html=True,
            )
            if leg.get("sample"):
                st.caption(f"Season sample: {leg['sample']}")
            st.caption(leg["opponent"] if leg.get("opponent") else "No opponent on the next game")
            st.caption(f"Price from {book_label(leg['book'])}")
        if remove:
            continue
        kept.append(leg)
    if len(kept) != len(slip):
        st.session_state.slip = kept
        st.rerun()
    active = [leg for leg in slip if leg.get("probability") is not None]
    if not active:
        return
    hit = slip_chance(active)
    note = same_game_note(active)
    if note:
        st.caption(note)
    st.caption("The vs-opponent chance scales each logged game by how much that team scores or allows versus the league. The from-stats price uses that chance. A posted book price stays the book's number.")
    st.text_input("Ticket price", placeholder="9.75 or +450", key="slip_price", help=TIPS["Ticket price"])
    try:
        ticket = _slip_ticket(stake, balance)
    except ValueError as exc:
        st.error(str(exc))
        return
    if ticket is None:
        return
    pieces = " × ".join(percent(leg["probability"]) for leg in active)
    st.markdown(
        "<div class='ticket-stats'>"
        + _stat("Together", percent(hit))
        + _stat("Odds", f"{format_american(ticket.american_odds)} / {ticket.decimal_odds:.2f}")
        + _stat("Payout", money(ticket.potential_payout))
        + _stat("Profit", money(ticket.net_profit_if_win))
        + "</div>",
        unsafe_allow_html=True,
    )
    st.caption(pieces)
    problem = exposure_message(stake, balance)
    if problem:
        st.info(problem)
    if ticket.exposure_warning and ticket.exposure_warning != problem:
        st.warning(ticket.exposure_warning)
    sports = {leg["sport"] for leg in active}
    slip_sport = next(iter(sports)) if len(sports) == 1 else "MIX"
    signature = tuple(leg.leg_id for leg in ticket.legs) + ("Custom", round(ticket.stake, 2), round(ticket.decimal_odds, 3))
    saved = st.session_state.setdefault("saved_signatures", set())
    if signature in saved:
        st.success("This slip is already in the ledger.")
        return
    if st.button("Save slip (paper)", type="primary"):
        _persist(ticket, placed_real=False, odds_source="custom", signature=signature, sport=slip_sport)
    if st.button("Save slip (real wager)"):
        _persist(ticket, placed_real=True, odds_source="custom", signature=signature, sport=slip_sport)


def main() -> None:
    inject_css()
    _scheduler_started()
    if not st.session_state.get("account"):
        render_login()
        return
    account = st.session_state.account
    if str(account.get("username", "")).lower() == "hvineypal":
        st.session_state.owner_override = True
        st.session_state.owner_unlocked = True
    allowed, denial = access_allowed(account.get("username", ""))
    if not allowed and not st.session_state.get("owner_override"):
        st.error(denial or UNPAID_MESSAGE)
        st.caption("Created and Owned by Harvir Vineypal")
        if os.getenv("PUCKEDGE_ADMIN_PASSWORD", "").strip():
            typed = st.text_input("Owner override", type="password")
            if st.button("Enter as owner"):
                if password_matches(typed):
                    st.session_state.owner_override = True
                    st.session_state.owner_unlocked = True
                    st.rerun()
                else:
                    st.error("Wrong password.")
        if st.button("Log out"):
            st.session_state.account = None
            st.session_state.skip_auto_login = True
            st.rerun()
        return
    account = st.session_state.account
    page = render_top(account)
    controls = current_controls()
    if controls["max_stake"] is None and not st.session_state.get("limits_skipped"):
        _limits_dialog(controls["balance"])
    board = current_board(
        controls["date"],
        controls["live"],
        controls["odds_key"],
        controls["sportsbook"],
        controls["sport"],
        controls["refresh"],
    )
    if page == "Record":
        render_ledger()
        render_analytics()
    elif page == "Setup":
        render_setup(account)
    elif page == "Omni":
        render_omniscreener(_slate_boards(controls, board), controls)
    else:
        slate = _slate_boards(controls, board)
        render_scoreboard(slate, controls["date"].isoformat())
        center, right = st.columns([68, 32], gap="large")
        with center:
            render_picker(controls, board)
            find = st.radio("Find", ["Tier", "Same game", "Hit a number", "Paste", "100% Hit Rates"], horizontal=True, key="find_mode")
            if find == "100% Hit Rates":
                render_hit_rates_mode(slate, controls)
            elif find == "Tier":
                tier_sports = st.multiselect("Leagues", ["NHL", "NFL", "NBA"], default=["NHL", "NFL", "NBA"], key="tier_sports")
                if not tier_sports:
                    st.warning("Pick at least one league.")
                else:
                    tier_boards = [slate.get(sport) or _board_for_sport(controls, sport) for sport in tier_sports]
                    render_builder(tier_boards, controls["balance"], controls["stake"])
            elif find == "Same game":
                sgp_sport = st.radio("Sport", ["NHL", "NFL", "NBA"], horizontal=True, key="sgp_sport")
                sgp_board = slate.get(sgp_sport) or board
                render_sgp(sgp_board, controls["stake"])
            elif find == "Hit a number":
                render_target(controls)
            else:
                render_boost(board, controls["stake"])
                render_my_parlay(controls)
        with right:
            st.markdown('<div class="ticket-pin"></div>', unsafe_allow_html=True)
            render_slip(controls["stake"], controls["balance"])
    if st.session_state.get("entity_popup"):
        _entity_dialog(str(st.session_state.entity_popup))
    st.markdown('<p class="credit">Created and Owned by Harvir Vineypal</p>', unsafe_allow_html=True)


main()
