"""SQLite tables for tickets, legs, and the bankroll ledger."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Parlay(Base):
    __tablename__ = "parlays"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    risk_tier: Mapped[str] = mapped_column(String, index=True)
    total_american_odds: Mapped[int] = mapped_column(Integer)
    total_decimal_odds: Mapped[float] = mapped_column(Float)
    stake_amount: Mapped[float] = mapped_column(Float)
    potential_payout: Mapped[float] = mapped_column(Float)
    user_placed_bet: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String, default="PENDING", index=True)
    settled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    net_profit: Mapped[float] = mapped_column(Float, default=0.0)
    closing_line_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    rationale_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    entry_edge: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev: Mapped[float | None] = mapped_column(Float, nullable=True)
    synergy_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    odds_source: Mapped[str | None] = mapped_column(String, nullable=True)
    settled_decimal_odds: Mapped[float | None] = mapped_column(Float, nullable=True)
    settled_american_odds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    legs: Mapped[list[ParlayLeg]] = relationship(back_populates="parlay", cascade="all, delete-orphan")


class ParlayLeg(Base):
    __tablename__ = "parlay_legs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    parlay_id: Mapped[str] = mapped_column(ForeignKey("parlays.id"), index=True)
    game_id: Mapped[str] = mapped_column(String, index=True)
    matchup: Mapped[str] = mapped_column(String)
    game_date: Mapped[str] = mapped_column(String)
    market_type: Mapped[str] = mapped_column(String)
    target_entity: Mapped[str] = mapped_column(String)
    selection: Mapped[str] = mapped_column(String)
    line: Mapped[float | None] = mapped_column(Float, nullable=True)
    american_odds: Mapped[int] = mapped_column(Integer)
    model_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    fair_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    ev: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_result: Mapped[float | None] = mapped_column(Float, nullable=True)
    leg_status: Mapped[str] = mapped_column(String, default="PENDING")
    settlement_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    side: Mapped[str] = mapped_column(String, default="")
    player_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    team_abbrev: Mapped[str | None] = mapped_column(String, nullable=True)
    bookmaker: Mapped[str | None] = mapped_column(String, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    parlay: Mapped[Parlay] = relationship(back_populates="legs")


class BankrollState(Base):
    __tablename__ = "bankroll_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    balance: Mapped[float] = mapped_column(Float, default=1000.0)
    initial_balance: Mapped[float] = mapped_column(Float, default=1000.0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class BankrollTransaction(Base):
    __tablename__ = "bankroll_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    parlay_id: Mapped[str | None] = mapped_column(String, nullable=True)
    amount: Mapped[float] = mapped_column(Float)
    balance_after: Mapped[float] = mapped_column(Float)
    kind: Mapped[str] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
