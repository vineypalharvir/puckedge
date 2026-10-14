"""SQLite engine and session factory."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from config import database_path
from src.tracker.models import Base

_engine = None
_Session: sessionmaker[Session] | None = None
_bound: Path | None = None


def configure(db_path: Path | None = None) -> sessionmaker[Session]:
    global _engine, _Session, _bound
    path = Path(db_path) if db_path is not None else database_path()
    if _Session is not None and _bound == path:
        return _Session
    if _engine is not None:
        _engine.dispose()
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _pragma(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    Base.metadata.create_all(engine)
    _engine = engine
    _bound = path
    _Session = sessionmaker(bind=engine, expire_on_commit=False)
    return _Session


def open_session(db_path: Path | None = None) -> Session:
    factory = configure(db_path)
    return factory()
