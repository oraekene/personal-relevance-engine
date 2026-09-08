"""Ticket 23 commit 1: tenant-scoped JSON API + bearer-token gate."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from pre.change_corpus import FirehoseEntry, ingest_entries
from pre.digest import assemble_digest
from pre.intake import apply_intake_dict
from pre.judge import ScriptedJudge
from pre.models import Change, DigestItem, Tool, VerdictLog
from pre.retrieval import index_all
from pre.scoring import judge_change


@pytest.fixture()
def client(session: Session):
    from fastapi.testclient import TestClient

    from pre.web import create_app

    engine = session.get_bind()

    def factory() -> Session:
        return sessionmaker(bind=engine, expire_on_commit=False)()

    return TestClient(create_app(factory))


@pytest.fixture()
def authed_client(client, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRE_API_TOKEN", "secret")
    client.headers.update({"Authorization": "Bearer secret"})
    return client


def _seeded_item(session: Session) -> DigestItem:
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
    assemble_digest(session, "daily")
    session.expire_all()
    return session.query(DigestItem).one()


def test_api_digest_lists_items(client, session: Session) -> None:
    _seeded_item(session)

    response = client.get("/api/digest/daily")

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "daily"
    assert body["mode"] in ("shadow", "live")
    assert len(body["items"]) == 1
    assert body["items"][0]["entity_label"] == "Apollo"
    assert body["items"][0]["verdict"] is None


def test_api_unknown_kind_404(client) -> None:
    assert client.get("/api/digest/hourly").status_code == 404


def test_api_verdict_roundtrip(client, session: Session) -> None:
    item = _seeded_item(session)

    response = client.post("/api/verdict", json={"item_id": item.id, "choice": "act"})

    assert response.status_code == 200
    assert response.json()["verdict"] == "act"
    assert isinstance(response.json()["profile_version"], int)
    session.expire_all()
    assert session.get(DigestItem, item.id).verdict == "act"  # type: ignore[union-attr]
    log = session.query(VerdictLog).one()
    assert log.channel == "api"

    assert client.post("/api/verdict", json={"item_id": item.id, "choice": "dismiss"}).status_code == 409


def test_api_verdict_rejects_bad_input(client, session: Session) -> None:
    _seeded_item(session)

    assert client.post("/api/verdict", json={"item_id": 1, "choice": "maybe"}).status_code == 400
    assert client.post("/api/verdict", json={"item_id": 9999, "choice": "act"}).status_code == 404


def test_api_matrix_lists_34_cells(client, session: Session) -> None:
    _seeded_item(session)

    response = client.get("/api/matrix")

    assert response.status_code == 200
    assert len(response.json()["cells"]) == 34


def test_api_ops_contains_dashboard(client, session: Session) -> None:
    _seeded_item(session)

    response = client.get("/api/ops")

    assert response.status_code == 200
    assert "OPS DASHBOARD" in response.json()["dashboard"]


def test_api_token_enforced(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRE_API_TOKEN", "secret")

    assert client.get("/api/digest/daily").status_code == 401
    client.headers.update({"Authorization": "Bearer wrong"})
    assert client.get("/api/digest/daily").status_code == 401
    client.headers.update({"Authorization": "Bearer secret"})
    assert client.get("/api/digest/daily").status_code == 200


def test_pages_stay_open_with_token_set(authed_client) -> None:
    assert authed_client.get("/").status_code == 200
