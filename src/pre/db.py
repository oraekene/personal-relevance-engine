"""Database engine/session helpers.

Production target is Postgres (+pgvector in later tickets); SQLite is the local dev/test
default so the whole test suite runs with zero infrastructure.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from pre.models import Base

DEFAULT_DB_URL = "sqlite:///pre.db"


def make_engine(url: str = DEFAULT_DB_URL) -> Engine:
    if url.startswith("sqlite"):
        return create_engine(url, connect_args={"check_same_thread": False})
    return create_engine(url)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
