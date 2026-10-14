"""Save tickets, move the bankroll, and summarize the ledger."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from config import DEFAULT_BANKROLL
from src.api.data_models import BuiltParlay
from src.tracker.models import BankrollState, BankrollTransaction, Parlay, ParlayLeg
from src.tracker.settlement import grade_leg, rollup_ticket


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
    delta = round(amount - state.balance, 2)
    state.balance = round(amount, 2)
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
    state.balance = round(state.balance + amount, 2)
    state.updated_at = _now()
    session.add(
        BankrollTransaction(
            created_at=_now(),
            parlay_id=parlay_id,
            amount=round(amount, 2),
            balance_after=state.balance,
            kind=kind,
            note=note,
        )
    )


def save_parlay(session: Session, built: BuiltParlay, *, placed_real: bool, odds_source: str, sport: str = "NHL") -> Parlay:
    parlay_id = uuid.uuid4().hex
    row = Parlay(
        id=parlay_id,
        created_at=_now(),
        risk_tier=built.risk_tier,
        total_american_odds=built.american_odds,
        total_decimal_odds=built.decimal_odds,
        stake_amount=built.stake,
        potential_payout=built.potential_payout,
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
                sport=sport,
            )
        )
    if placed_real and built.stake > 0:
        _move_bankroll(session, -built.stake, "STAKE", parlay_id, f"Real stake on {built.risk_tier} ticket")
    session.commit()
    return row


def list_parlays(session: Session) -> list[Parlay]:
    return list(
        session.scalars(
            select(Parlay).options(selectinload(Parlay.legs)).order_by(Parlay.created_at.desc())
        ).all()
    )


def settle_pending(session: Session, fetch_boxscore) -> int:
    pending = list(
        session.scalars(
            select(Parlay)
            .where(Parlay.status == "PENDING")
            .options(selectinload(Parlay.legs))
        ).all()
    )
    changed = 0
    for parlay in pending:
        boxes: dict[str, object] = {}
        for leg in parlay.legs:
            if leg.leg_status != "PENDING":
                continue
            if leg.game_id not in boxes:
                try:
                    sport = getattr(parlay, "sport", None) or "NHL"
                    try:
                        boxes[leg.game_id] = fetch_boxscore(leg.game_id, sport)
                    except TypeError:
                        boxes[leg.game_id] = fetch_boxscore(leg.game_id)
                except Exception as exc:
                    boxes[leg.game_id] = None
                    leg.settlement_notes = f"Box score unavailable: {exc}"
            status, actual, notes = grade_leg(leg, boxes[leg.game_id])
            if status != "PENDING":
                leg.leg_status = status
                leg.actual_result = actual
                leg.settlement_notes = notes
        new_status, profit, decimal_odds, american = rollup_ticket(parlay.legs, parlay.stake_amount)
        if new_status == parlay.status:
            continue
        previous = parlay.status
        parlay.status = new_status
        parlay.net_profit = profit
        if decimal_odds is not None:
            parlay.settled_decimal_odds = decimal_odds
            parlay.settled_american_odds = american
            if abs(decimal_odds - parlay.total_decimal_odds) > 1e-6:
                parlay.potential_payout = round(parlay.stake_amount * decimal_odds, 2)
        if new_status != "PENDING":
            parlay.settled_at = _now()
        if previous == "PENDING" and parlay.user_placed_bet:
            if new_status == "WON":
                _move_bankroll(
                    session,
                    parlay.stake_amount + profit,
                    "PAYOUT",
                    parlay.id,
                    "Winning ticket payout",
                )
            elif new_status == "PUSHED":
                _move_bankroll(session, parlay.stake_amount, "REFUND", parlay.id, "Pushed ticket refund")
        changed += 1
    session.commit()
    return changed


def performance(session: Session) -> dict:
    state = ensure_bankroll(session)
    rows = list_parlays(session)
    settled = [row for row in rows if row.status in {"WON", "LOST", "PUSHED"}]
    wins = [row for row in settled if row.status == "WON"]
    losses = [row for row in settled if row.status == "LOST"]
    pushes = [row for row in settled if row.status == "PUSHED"]
    wagered = sum(row.stake_amount for row in settled)
    profit = sum(row.net_profit for row in settled)
    decided = len(wins) + len(losses)
    real = [row for row in settled if row.user_placed_bet]
    paper = [row for row in settled if not row.user_placed_bet]
    clv_values = [row.closing_line_value for row in rows if row.closing_line_value is not None]
    edges = [row.entry_edge for row in rows if row.entry_edge is not None]

    def _bucket(group: list[Parlay]) -> dict:
        decided_rows = [row for row in group if row.status in {"WON", "LOST"}]
        won = sum(1 for row in decided_rows if row.status == "WON")
        stake_sum = sum(row.stake_amount for row in group)
        net = sum(row.net_profit for row in group)
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
                "real_bankroll": round(real_running, 2),
                "paper_profit": round(paper_running, 2),
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
