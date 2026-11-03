"""Save tickets, move the bankroll, and summarize the ledger."""

from __future__ import annotations

import logging
import math
import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from config import DEFAULT_BANKROLL
from src.api.data_models import BuiltParlay
from src.tracker.models import BankrollState, BankrollTransaction, Parlay, ParlayLeg
from src.tracker.resolve import LegResolver, call_fetch
from src.tracker.settlement import grade_leg, rollup_ticket
from src.engine.devig import american_to_implied, is_valid_american
from src.utils.money import payout_amount, to_cents

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def ensure_bankroll(session: Session) -> BankrollState:
    state = session.get(BankrollState, 1)
    if state is None:
        state = BankrollState(id=1, balance=DEFAULT_BANKROLL, initial_balance=DEFAULT_BANKROLL, updated_at=_now())
        session.add(state)
        session.commit()
    return state


def get_balance(session: Session) -> float:
    return float(ensure_bankroll(session).balance)


def set_balance(session: Session, amount: float, note: str = "Manual bankroll update") -> float:
    state = ensure_bankroll(session)
    if amount is None or not math.isfinite(float(amount)) or float(amount) < 0:
        raise ValueError("Bankroll cannot be negative.")
    delta = to_cents(float(amount) - state.balance)
    state.balance = to_cents(amount)
    state.updated_at = _now()
    ticket_count = session.scalar(select(func.count()).select_from(Parlay)) or 0
    if ticket_count == 0:
        state.initial_balance = state.balance
    session.add(
        BankrollTransaction(
            created_at=_now(),
            amount=delta,
            balance_after=state.balance,
            kind="ADJUSTMENT",
            note=note,
        )
    )
    session.commit()
    return state.balance


def _move_bankroll(session: Session, amount: float, kind: str, parlay_id: str, note: str) -> None:
    state = ensure_bankroll(session)
    state.balance = to_cents(state.balance + amount)
    state.updated_at = _now()
    session.add(
        BankrollTransaction(
            created_at=_now(),
            parlay_id=parlay_id,
            amount=to_cents(amount),
            balance_after=state.balance,
            kind=kind,
            note=note,
        )
    )


LEAGUES = {"NHL", "NBA", "NFL"}


def _leg_sport_at_save(leg, ticket_sport: str) -> str:
    """The leg's own league when it knows it, otherwise the ticket's."""
    own = str(getattr(leg, "sport", None) or "").upper()
    return own if own in LEAGUES else ticket_sport


def save_parlay(session: Session, built: BuiltParlay, *, placed_real: bool, odds_source: str, sport: str = "NHL") -> Parlay:
    stake = to_cents(built.stake)
    if placed_real:
        if not (stake > 0):
            raise ValueError("A real wager has to be above $0.")
        balance = get_balance(session)
        if balance <= 0:
            raise ValueError("Set a bankroll above $0 before logging a real wager.")
        if stake > balance + 1e-9:
            raise ValueError(f"This wager is more than the ${balance:,.2f} bankroll. Lower it or update the bankroll first.")
    parlay_id = uuid.uuid4().hex
    row = Parlay(
        id=parlay_id,
        created_at=_now(),
        risk_tier=built.risk_tier,
        total_american_odds=built.american_odds,
        total_decimal_odds=built.decimal_odds,
        stake_amount=stake,
        potential_payout=payout_amount(stake, built.decimal_odds),
        user_placed_bet=placed_real,
        status="PENDING",
        net_profit=0.0,
        closing_line_value=None,
        rationale_summary=built.rationale_summary,
        entry_edge=built.entry_edge,
        model_prob=built.model_prob,
        ev=built.ev,
        synergy_notes="\n".join(built.synergy_notes),
        odds_source=odds_source,
        sport=sport,
    )
    session.add(row)
    for index, leg in enumerate(built.legs):
        session.add(
            ParlayLeg(
                id=f"{parlay_id}-{index}-{leg.leg_id}",
                parlay_id=parlay_id,
                game_id=leg.game_id,
                matchup=leg.matchup,
                game_date=leg.game_date,
                market_type=leg.market_type,
                target_entity=leg.target_entity,
                selection=leg.selection,
                line=leg.line,
                american_odds=leg.american_odds,
                model_prob=leg.model_prob,
                fair_prob=leg.fair_prob,
                ev=leg.ev,
                leg_status="PENDING",
                side=leg.side,
                player_id=leg.player_id,
                team_abbrev=leg.team_abbrev,
                bookmaker=leg.bookmaker,
                rationale=leg.rationale,
                sport=_leg_sport_at_save(leg, sport),
            )
        )
    if placed_real and stake > 0:
        _move_bankroll(session, -stake, "STAKE", parlay_id, f"Real stake on {built.risk_tier} ticket")
    session.commit()
    return row


def list_parlays(session: Session) -> list[Parlay]:
    return list(
        session.scalars(
            select(Parlay).options(selectinload(Parlay.legs)).order_by(Parlay.created_at.desc())
        ).all()
    )


def settle_pending(session: Session, fetch_boxscore, fetch_closing=None, find_game=None) -> int:
    """Grade pending tickets from final box scores and move the bankroll.

    fetch_boxscore(game_id, sport) or fetch_boxscore(game_id) returns a box score.
    fetch_closing(leg) may return (closing_line, closing_odds) when a feed has them;
    without it the closing columns stay NULL.
    find_game(sport, away, home, days) returns a game id for a leg that has no usable
    saved id (custom slips, typed legs); without it those legs stay pending.
    """
    pending = list(
        session.scalars(
            select(Parlay)
            .where(Parlay.status == "PENDING")
            .options(selectinload(Parlay.legs))
        ).all()
    )
    resolver = LegResolver(fetch_boxscore, find_game)
    changed = 0
    for parlay in pending:
        for leg in parlay.legs:
            if leg.leg_status != "PENDING":
                continue
            box, sport, game_id, note = resolver.resolve(
                leg, getattr(parlay, "sport", None), parlay.created_at, saved_sport=route_leg(leg, parlay)
            )
            if box is not None:
                leg.game_id = game_id
                leg.sport = sport
            status, actual, notes = grade_leg(leg, box)
            if status != "PENDING":
                leg.leg_status = status
                leg.actual_result = actual
                leg.settlement_notes = notes
                leg.settled_at = _now()
                _store_closing(leg, fetch_closing)
            else:
                leg.settlement_notes = note or notes
        try:
            new_status, profit, decimal_odds, american = rollup_ticket(parlay.legs, parlay.stake_amount)
        except ValueError as exc:
            logger.warning("Ticket %s could not be settled: %s", parlay.id, exc)
            continue
        if new_status == parlay.status:
            continue
        previous = parlay.status
        parlay.status = new_status
        parlay.net_profit = profit
        if decimal_odds is not None:
            parlay.settled_decimal_odds = decimal_odds
            parlay.settled_american_odds = american
            if abs(decimal_odds - parlay.total_decimal_odds) > 1e-6:
                parlay.potential_payout = payout_amount(parlay.stake_amount, decimal_odds)
        if new_status != "PENDING":
            parlay.settled_at = _now()
            parlay.closing_line_value = _ticket_clv(parlay.legs)
        if previous == "PENDING" and parlay.user_placed_bet:
            if new_status == "WON":
                _move_bankroll(
                    session,
                    to_cents(parlay.stake_amount + profit),
                    "PAYOUT",
                    parlay.id,
                    "Winning ticket payout",
                )
            elif new_status == "PUSHED":
                _move_bankroll(session, parlay.stake_amount, "REFUND", parlay.id, "Pushed ticket refund")
        changed += 1
    session.commit()
    return changed


_ESPN_ID = re.compile(r"^\d{9,}$")
_NHL_ID = re.compile(r"^\d{10}$")


def route_leg(leg, parlay) -> str | None:
    """League to fetch this leg's box score from, or None when it has no real event id.

    NHL game ids are 10 digits (2025020123). ESPN event ids for NBA and NFL are 9
    digits or more. Player ids (4 to 7 digits) and typed or mock ids never match,
    which stops a player id from being sent to a box score endpoint as a game id.
    The leg's own league wins over the ticket's, so mixed-league tickets route right.
    """
    if str(getattr(leg, "market_type", "")).upper() == "CUSTOM":
        return None
    game_id = str(getattr(leg, "game_id", "") or "").strip()
    if _NHL_ID.match(game_id):
        return "NHL"
    if not _ESPN_ID.match(game_id):
        return None
    for candidate in (getattr(leg, "sport", None), getattr(parlay, "sport", None)):
        league = str(candidate or "").upper()
        if league in {"NBA", "NFL"}:
            return league
    return None


_call_fetch = call_fetch


def _store_closing(leg: ParlayLeg, fetch_closing) -> None:
    """Record the closing price only when a provider actually has one."""
    if fetch_closing is None:
        return
    try:
        found = fetch_closing(leg)
    except Exception:
        return
    if not found:
        return
    line, odds = found
    leg.closing_line = None if line is None else float(line)
    leg.closing_odds = None if odds is None or not is_valid_american(odds) else int(odds)


def _ticket_clv(legs) -> float | None:
    """Average per-leg closing-line value, only when every leg has a real closing price."""
    values = []
    for leg in legs:
        if leg.leg_status in {"PUSH", "VOID"}:
            continue
        if leg.closing_odds is None or not is_valid_american(leg.closing_odds) or not is_valid_american(leg.american_odds):
            return None
        values.append(american_to_implied(leg.closing_odds) - american_to_implied(leg.american_odds))
    if not values:
        return None
    return sum(values) / len(values)


def performance(session: Session) -> dict:
    state = ensure_bankroll(session)
    rows = list_parlays(session)
    settled = [row for row in rows if row.status in {"WON", "LOST", "PUSHED"}]
    wins = [row for row in settled if row.status == "WON"]
    losses = [row for row in settled if row.status == "LOST"]
    pushes = [row for row in settled if row.status == "PUSHED"]
    wagered = to_cents(sum(row.stake_amount for row in settled))
    profit = to_cents(sum(row.net_profit for row in settled))
    decided = len(wins) + len(losses)
    real = [row for row in settled if row.user_placed_bet]
    paper = [row for row in settled if not row.user_placed_bet]
    clv_values = [row.closing_line_value for row in rows if row.closing_line_value is not None]
    edges = [row.entry_edge for row in rows if row.entry_edge is not None]

    def _bucket(group: list[Parlay]) -> dict:
        decided_rows = [row for row in group if row.status in {"WON", "LOST"}]
        won = sum(1 for row in decided_rows if row.status == "WON")
        stake_sum = to_cents(sum(row.stake_amount for row in group))
        net = to_cents(sum(row.net_profit for row in group))
        return {
            "tickets": len(group),
            "wins": won,
            "losses": len(decided_rows) - won,
            "hit_rate": (won / len(decided_rows)) if decided_rows else None,
            "wagered": stake_sum,
            "net_profit": net,
            "roi": (net / stake_sum) if stake_sum else None,
        }

    by_tier = []
    for tier in ("Conservative", "Balanced", "Aggressive", "Moonshot", "Krazy"):
        summary = _bucket([row for row in settled if row.risk_tier == tier])
        summary["tier"] = tier
        by_tier.append(summary)

    market_counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for row in rows:
        for leg in row.legs:
            if leg.leg_status == "WON":
                market_counts[leg.market_type][0] += 1
            elif leg.leg_status == "LOST":
                market_counts[leg.market_type][1] += 1
    by_market = []
    for market, (won, lost) in sorted(market_counts.items()):
        total = won + lost
        by_market.append(
            {
                "market": market,
                "wins": won,
                "losses": lost,
                "hit_rate": (won / total) if total else None,
            }
        )

    ordered = sorted(
        [row for row in settled if row.status in {"WON", "LOST"}],
        key=lambda row: row.settled_at or row.created_at,
    )
    best_win = worst_loss = current_win = current_loss = 0
    for row in ordered:
        if row.status == "WON":
            current_win += 1
            current_loss = 0
        else:
            current_loss += 1
            current_win = 0
        best_win = max(best_win, current_win)
        worst_loss = max(worst_loss, current_loss)

    curve = []
    real_running = state.initial_balance
    paper_running = 0.0
    for row in sorted(settled, key=lambda item: item.settled_at or item.created_at):
        if row.user_placed_bet:
            real_running += row.net_profit
        else:
            paper_running += row.net_profit
        curve.append(
            {
                "settled_at": (row.settled_at or row.created_at).isoformat(),
                "label": row.risk_tier,
                "real_bankroll": to_cents(real_running),
                "paper_profit": to_cents(paper_running),
            }
        )

    return {
        "balance": state.balance,
        "initial_balance": state.initial_balance,
        "tickets": len(rows),
        "pending": sum(1 for row in rows if row.status == "PENDING"),
        "wins": len(wins),
        "losses": len(losses),
        "pushes": len(pushes),
        "wagered": wagered,
        "net_profit": profit,
        "roi": (profit / wagered) if wagered else None,
        "hit_rate": (len(wins) / decided) if decided else None,
        "avg_clv": (sum(clv_values) / len(clv_values)) if clv_values else None,
        "avg_entry_edge": (sum(edges) / len(edges)) if edges else None,
        "by_tier": by_tier,
        "real": _bucket(real),
        "paper": _bucket(paper),
        "by_market": by_market,
        "best_win_streak": best_win,
        "worst_loss_streak": worst_loss,
        "curve": curve,
        "parlays": rows,
    }
