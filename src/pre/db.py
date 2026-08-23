"""Database engine/session helpers.

Production target is Postgres (+pgvector in later tickets); SQLite is the local dev/test
default so the whole test suite runs with zero infrastructure.
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from pre.models import Base

DEFAULT_DB_URL = "sqlite:///pre.db"


def make_engine(url: str = DEFAULT_DB_URL) -> Engine:
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        # Pure in-memory SQLite must share ONE connection across threads
        # (the web surface serves requests from a worker thread):
        if url.rstrip("/").endswith(":memory:") or url == "sqlite://":
            return create_engine(
                url, connect_args=connect_args, poolclass=StaticPool
            )
        return create_engine(url, connect_args=connect_args)
    return create_engine(url)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False)
