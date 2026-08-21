from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from pre.db import init_db, make_engine, make_session_factory


@pytest.fixture()
def engine():
    engine = make_engine("sqlite://")
    init_db(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture()
def session(engine) -> Session:
    factory = make_session_factory(engine)
    s = factory()
    try:
        yield s
    finally:
        s.close()
