"""PuckEdge Parlay Engine dashboard.

Run from the project root:

    .venv\\Scripts\\python.exe -m streamlit run app.py
"""

from __future__ import annotations

import os
from datetime import date, datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from src.api.data_models import BuiltParlay, PricedBoard
from config import BOOK_LABELS, ROOT
from src.engine.boost import grade_boost
from src.engine.devig import format_american
from src.engine.generator import PROFILES, generate_parlays
from src.services.slate import load_board
from src.tracker.database import open_session
from src.tracker.repository import get_balance, list_parlays, performance, save_parlay, set_balance
from src.tracker.scheduler import run_settlement_once, start_scheduler
from src.updater import peek_update

st.set_page_config(
    page_title="PuckEdge Parlay Engine",
    page_icon="🏒",
    layout="wide",
    initial_sidebar_state="expanded",
)


def inject_css() -> None:
    st.markdown(
        """
        <style>
        .hero { padding: 0.2rem 0 0.8rem 0; }
        .hero h1 {
            font-size: 2.5rem;
            letter-spacing: 0.04em;
            margin: 0;
            font-weight: 750;
        }
        .hero h1 span { color: #3DDCFF; }
        .hero p { color: #93A4BD; margin: 0.2rem 0 0 0; }
        .hero .credit { color: #3DDCFF; margin-top: 0.35rem; font-size: 0.95rem; }
        .pill {
            display: inline-block;
            padding: 0.12rem 0.55rem;
            border-radius: 999px;
            font-size: 0.78rem;
            font-weight: 650;
            margin-right: 0.35rem;
            background: #163044;
            color: #D7F6FF;
        }
        .pill.warn { background: #4A3410; color: #FDE68A; }
        .pill.good { background: #123524; color: #86EFAC; }
        .pill.bad { background: #4A1D1D; color: #FCA5A5; }
        .fine { color: #93A4BD; font-size: 0.92rem; }
        div[data-testid="stMetric"] {
            background: #10192A;
            border: 1px solid #1C2B42;
            padding: 0.7rem 0.8rem;
            border-radius: 12px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


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


def money(value: float) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


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
                "bar": {"color": "#3DDCFF"},
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


def current_board(slate_date: date, live: bool, odds_key: str, sportsbook: str, refresh: bool) -> PricedBoard:
    token = (slate_date.isoformat(), live, bool(odds_key), sportsbook)
    if refresh or st.session_state.get("board_token") != token:
        with st.spinner("Loading the NHL slate and pricing the board..."):
            st.session_state.board = load_board(
                slate_date.isoformat(),
                prefer_live=live,
                odds_api_key=odds_key or None,
                sportsbook=sportsbook,
            )
        st.session_state.board_token = token
        st.session_state.tickets = []
    return st.session_state.board


def render_sidebar() -> dict:
    st.sidebar.markdown("### PuckEdge")
    st.sidebar.caption("Local NHL board, +EV parlays, and a graded ledger.")
    st.sidebar.caption("Created by Harvir Vineypal for larping purposes.")
    slate_date = st.sidebar.date_input("Slate date", value=date.today())
    live = st.sidebar.toggle(
        "Live NHL feed",
        value=os.getenv("PUCKEDGE_LIVE", "1") != "0",
        help="Turn this off to use the built-in offline slate.",
    )
    previous = st.session_state.get("board")
    options = list(getattr(previous, "available_books", None) or ["bet365", "draftkings", "fanduel", "pinnacle", "synthetic-bet365"])
    if "sportsbook" not in st.session_state:
        st.session_state.sportsbook = "bet365"
    if st.session_state.sportsbook not in options:
        options = [st.session_state.sportsbook, *[book for book in options if book != st.session_state.sportsbook]]
    sportsbook = st.sidebar.selectbox(
        "Sportsbook",
        options,
        index=options.index(st.session_state.sportsbook),
        format_func=book_label,
        help="US, UK, and EU feeds. Bet365 is usually UK/EU. DraftKings and FanDuel are US.",
    )
    st.session_state.sportsbook = sportsbook
    update_line = cached_update_status()
    if update_line:
        st.sidebar.caption(update_line)
    odds_key = st.sidebar.text_input(
        "Odds API key",
        value=os.getenv("ODDS_API_KEY", ""),
        type="password",
        help="Optional. Without a key the board uses synthetic Bet365 prices.",
    )
    refresh = st.sidebar.button("Refresh slate", width="stretch")
    session = open_session()
    try:
        balance = get_balance(session)
    finally:
        session.close()
    st.sidebar.divider()
    st.sidebar.metric("Current bankroll", money(balance))
    if "bankroll_editor" not in st.session_state:
        st.session_state.bankroll_editor = float(balance)
    bankroll_input = st.sidebar.number_input("Set bankroll", min_value=0.0, step=50.0, key="bankroll_editor")
    if st.sidebar.button("Update bankroll", width="stretch"):
        session = open_session()
        try:
            set_balance(session, float(bankroll_input))
        finally:
            session.close()
        st.rerun()
    stake = st.sidebar.number_input("Target wager", min_value=0.0, value=25.0, step=5.0)
    st.sidebar.caption("Real wagers deduct the stake immediately. Paper tickets do not move the bankroll.")
    return {
        "date": slate_date,
        "live": live,
        "odds_key": odds_key.strip(),
        "sportsbook": sportsbook,
        "refresh": refresh,
        "balance": balance,
        "stake": float(stake),
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
    st.subheader("Today's NHL board")
    if not board.context.games:
        st.warning("No NHL games on this date.")
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
                if leg.game_id == game.game_id and leg.market_type == "TOTAL" and leg.side == "OVER" and leg.line in {5.5, 6.0, 6.5}
            ),
            None,
        )
        rows.append(
            {
                "Start": format_start(game.start_time_utc),
                "Matchup": f"{game.away.abbrev} @ {game.home.abbrev}",
                "State": game.game_state,
                "Away": f"{game.away.name} ({_starters(board, game.away.abbrev)})",
                "Home": f"{game.home.name} ({_starters(board, game.home.abbrev)})",
                "Form": f"{away.abbrev if away else game.away.abbrev}: {_record(board, game.away.abbrev)} | {home.abbrev if home else game.home.abbrev}: {_record(board, game.home.abbrev)}",
                "Moneyline": (
                    f"{game.away.abbrev} {format_american(away_ml.american_odds) if away_ml else '—'} / "
                    f"{game.home.abbrev} {format_american(home_ml.american_odds) if home_ml else '—'}"
                ),
                "Total": f"O/U {total.line:.1f} ({format_american(total.american_odds)})" if total else "—",
                "Signals": ", ".join(board.badges.get(game.game_id) or []) or "—",
                "Book": (home_ml or away_ml or total).bookmaker if (home_ml or away_ml or total) else board.odds_source,
            }
        )
    frame = pd.DataFrame(rows)
    st.dataframe(frame, width="stretch", hide_index=True)
    st.caption(f"Odds source: {board.odds_source}. Goalies are the current games-started leader until a starter is confirmed.")


def render_builder(board: PricedBoard, balance: float, stake: float) -> None:
    st.subheader("Parlay builder")
    descriptions = {
        "Conservative": "2 legs · about -120 to +160 · +1.5, team over 1.5, star 2+ shots · quarter Kelly, 2–3% of bankroll.",
        "Balanced": "3–4 legs · +250 to +500 · favorite moneylines, totals, shot and point props · eighth Kelly, 1–1.5%.",
        "Aggressive": "4–6 legs · +600 to +1500 · -1.5, dogs, anytime goals · about 0.5% of bankroll.",
        "Moonshot": "6–8 legs · +2000 to +10000 · multi-goal and plus-money props · 0.1–0.25%, suggestion capped at $10.",
        "Krazy": "4–8 legs · $1 returns $100 or more · dogs, anytime goals, wild props. The suggestion is a tiny slice of bankroll. Your target wager can be anything.",
    }
    if not board.context.games:
        st.warning("No NHL games on this date.")
        return
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
        st.plotly_chart(exposure_gauge(stake / balance if balance else 0), width="stretch")
    with guide_col:
        st.markdown("**Sizing guide**")
        low = balance * profile.stake_low
        high = balance * profile.stake_high
        cap = f" · hard cap {money(profile.stake_cap)}" if profile.stake_cap else ""
        st.write(f"Guideline for this tier: {money(low)} to {money(high)}{cap}.")
        st.write(f"Your wager is {percent(stake / balance if balance else None)} of bankroll.")
        if tier in {"Aggressive", "Moonshot", "Krazy"} and balance and stake / balance > 0.05:
            st.warning("This stake is over 5% of bankroll on a longshot tier. The cards below show the fractional Kelly suggestion.")

    game_frame = pd.DataFrame(
        [
            {
                "Include": True,
                "Matchup": f"{game.away.abbrev} @ {game.home.abbrev}",
                "Start": format_start(game.start_time_utc),
                "Signals": ", ".join(board.badges.get(game.game_id) or []) or "—",
                "game_id": game.game_id,
            }
            for game in board.context.games
        ]
    )
    edited = st.data_editor(
        game_frame,
        column_config={
            "Include": st.column_config.CheckboxColumn("Include", default=True),
            "game_id": None,
        },
        disabled=["Matchup", "Start", "Signals"],
        hide_index=True,
        width="stretch",
        key=f"games-{board.context.slate_date}-{board.context.source}",
    )
    selected = set(edited.loc[edited["Include"], "game_id"])
    if st.button("Generate optimal parlays", type="primary"):
        if len(selected) < 1:
            st.warning("Include at least one game.")
        else:
            with st.spinner("Searching correlated tickets..."):
                st.session_state.tickets = generate_parlays(
                    board.legs,
                    tier,
                    bankroll=balance,
                    stake=stake,
                    game_ids=selected,
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


def render_ticket(ticket: BuiltParlay, index: int, odds_source: str) -> None:
    with st.container(border=True):
        st.markdown(f"### Ticket {index} · {ticket.risk_tier}")
        flags = []
        if ticket.within_odds_band:
            flags.append('<span class="pill good">In odds band</span>')
        else:
            flags.append('<span class="pill warn">Outside odds band</span>')
        if ticket.filters_relaxed:
            flags.append('<span class="pill warn">Floor relaxed</span>')
        if ticket.synergy_score:
            flags.append(f'<span class="pill good">Synergy {ticket.synergy_score}</span>')
        st.markdown("".join(flags), unsafe_allow_html=True)
        metrics = st.columns(5)
        metrics[0].metric("Odds", format_american(ticket.american_odds))
        metrics[1].metric("Model win %", percent(ticket.model_prob))
        metrics[2].metric("Expected value", percent(ticket.ev))
        metrics[3].metric("Payout", money(ticket.potential_payout))
        metrics[4].metric("Profit if win", money(ticket.net_profit_if_win))
        st.write(ticket.rationale_summary)
        if ticket.risk_tier == "Krazy":
            st.markdown(
                f'<span class="pill warn">$1 returns {money(ticket.decimal_odds)}</span>',
                unsafe_allow_html=True,
            )
        for note in ticket.synergy_notes:
            st.markdown(f"- {note}")
        if ticket.exposure_warning:
            st.warning(ticket.exposure_warning)
        st.caption(
            f"Kelly suggestion {money(ticket.recommended_stake)} "
            f"({percent(ticket.kelly_fraction)} of bankroll). "
            f"Guideline {money(ticket.guideline_low)}–{money(ticket.guideline_high)}. "
            f"Average entry edge {percent(ticket.entry_edge)}. Book: {odds_source}."
        )
        leg_frame = pd.DataFrame(
            [
                {
                    "Leg": leg.selection,
                    "Matchup": leg.matchup,
                    "Odds": format_american(leg.american_odds),
                    "Model": percent(leg.model_prob),
                    "Fair": percent(leg.fair_prob),
                    "EV": percent(leg.ev),
                    "Book": leg.bookmaker,
                    "Why": leg.rationale,
                }
                for leg in ticket.legs
            ]
        )
        st.dataframe(leg_frame, width="stretch", hide_index=True)
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


def _persist(ticket: BuiltParlay, *, placed_real: bool, odds_source: str, signature: tuple) -> None:
    session = open_session()
    try:
        save_parlay(session, ticket, placed_real=placed_real, odds_source=odds_source)
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
                "When": row.created_at.strftime("%b %d, %I:%M %p"),
                "Tier": row.risk_tier,
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
            f"{row.risk_tier} · {format_american(row.total_american_odds)} · "
            f"{row.status} · {'Real' if row.user_placed_bet else 'Paper'}"
        )
        with st.expander(title):
            st.markdown(status_pill(row.status), unsafe_allow_html=True)
            if row.rationale_summary:
                st.write(row.rationale_summary)
            if row.synergy_notes:
                st.caption(row.synergy_notes.replace("\n", " · "))
            if row.settled_at is not None:
                st.caption(f"Settled {row.settled_at.strftime('%b %d, %I:%M %p')}.")
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
                        "Model": percent(leg.model_prob),
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
        "CLV stays blank until a later price snapshot is stored. Entry edge is model probability minus the no-vig price at the time the ticket was built. "
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
                    marker_color="#3DDCFF",
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
    columns[2].metric("Model hit chance", percent(grade.model_prob))
    payout = stake * grade.boosted_decimal
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
                        "Model hit %": percent(leg.model_prob),
                        "Lean": "Live" if leg.model_prob >= leg.implied_prob else "Fading",
                    }
                    for leg in grade.matched
                ]
            ),
            width="stretch",
            hide_index=True,
        )


def main() -> None:
    inject_css()
    _scheduler_started()
    st.markdown(
        """
        <div class="hero">
            <h1>PUCK<span>EDGE</span></h1>
            <p>NHL edge, Bet365-compatible prices, and a ledger that grades every ticket.</p>
            <p class="credit">Created by Harvir Vineypal for larping purposes.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
    controls = render_sidebar()
    board = current_board(
        controls["date"],
        controls["live"],
        controls["odds_key"],
        controls["sportsbook"],
        controls["refresh"],
    )
    builder, boost, slate, ledger, analytics = st.tabs(
        ["Parlay Builder", "Bet Boost", "NHL Board", "Ledger", "Analytics"]
    )
    with builder:
        render_builder(board, controls["balance"], controls["stake"])
    with boost:
        render_boost(board, controls["stake"])
    with slate:
        render_board(board)
    with ledger:
        render_ledger()
    with analytics:
        render_analytics()
    st.caption(
        "Created by Harvir Vineypal for larping purposes. "
        "PuckEdge is a local analytics and paper-tracking tool. It is not a sportsbook and it is not betting advice."
    )


main()
