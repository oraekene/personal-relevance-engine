from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pre.change_corpus import FirehoseEntry, ingest_entries
from pre.digest import assemble_digest
from pre.intake import apply_intake_dict
from pre.judge import ScriptedJudge
from pre.models import Change, DigestItem, Tool, VerdictLog
from pre.retrieval import index_all
from pre.scoring import judge_change
from pre.verdicts import record_verdict


@pytest.fixture()
def client(session: Session):
    from fastapi.testclient import TestClient

    from pre.web import create_app

    engine = session.get_bind()

    def factory() -> Session:
        return sessionmaker(bind=engine, expire_on_commit=False)()

    return TestClient(create_app(factory))


def _seed_and_assemble(session: Session) -> DigestItem:
    apply_intake_dict(
        session,
        {
            "dimensions": [
                {
                    "code": "business",
                    "goals": [
                        {
                            "title": "Use Apollo heavily",
                            "needs": [
                                {
                                    "title": "Apollo reliability",
                                    "activities": [
                                        {
                                            "title": "Work in Apollo daily",
                                            "tasks": [{"title": "Open Apollo",
                                                       "tools": ["Apollo"]}],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    ingest_entries(
        session,
        [FirehoseEntry(product_name="Apollo", title="Apollo pricing change for teams")],
        "lane",
    )
    index_all(session)
    change = session.query(Change).one()
    tool = session.query(Tool).one()
    judge_change(session, change.id, ScriptedJudge({("tool", tool.id): (90, "you rely on Apollo")}))
    items = assemble_digest(session, "daily")
    assert len(items) == 1
    session.expire_all()
    return session.query(DigestItem).one()


def test_overview_renders(client, session: Session) -> None:
    _seed_and_assemble(session)

    response = client.get("/")

    assert response.status_code == 200
    assert "Daily digest" in response.text


def test_digest_page_shows_items_and_verdict_buttons(client, session: Session) -> None:
    item = _seed_and_assemble(session)

    response = client.get(f"/digest/{item.digest_kind}")

    assert response.status_code == 200
    assert "Apollo" in response.text
    assert "you rely on Apollo" in response.text
    assert f"/item/{item.id}/verdict/act" in response.text


def test_verdict_link_records_via_web_channel(client, session: Session) -> None:
    item = _seed_and_assemble(session)

    response = client.get(f"/item/{item.id}/verdict/act", follow_redirects=False)

    assert response.status_code == 303
    session.expire_all()
    refreshed = session.query(DigestItem).one()
    assert refreshed.verdict == "act"
    log = session.scalars(select(VerdictLog)).one()
    assert log.channel == "web"


def test_second_verdict_returns_409(client, session: Session) -> None:
    item = _seed_and_assemble(session)
    record_verdict(session, item.id, "act", channel="web")

    response = client.get(f"/item/{item.id}/verdict/dismiss")

    assert response.status_code == 409


def test_unknown_kind_404(client) -> None:
    assert client.get("/digest/hourly").status_code == 404


def test_push_link_shape() -> None:
    from pre.web import push_link

    assert push_link("https://pre.example.com", 7) == (
        "https://pre.example.com/item/7/verdict/%s"
    )


def test_live_mode_marks_items_delivered_on_serve(
    client, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pre.coldstart import set_mode

    _seed_and_assemble(session)
    set_mode(session, "live")
    session.expire_all()

    client.get("/digest/daily")

    session.expire_all()
    item = session.query(DigestItem).one()
    assert item.delivered_at is not None
    delivered = (
        item.delivered_at.replace(tzinfo=UTC)
        if item.delivered_at.tzinfo is None
        else item.delivered_at
    )
    assert delivered <= datetime.now(UTC)
