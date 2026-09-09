"""Ticket 23 commit 4: takeout-file upload surface."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pre.models import SourceSyncState
from pre.queue import list_pending


@pytest.fixture()
def client(session: Session):
    from fastapi.testclient import TestClient

    from pre.web import create_app

    engine = session.get_bind()

    def factory() -> Session:
        return sessionmaker(bind=engine, expire_on_commit=False)()

    return TestClient(create_app(factory))


FIXTURES = Path(__file__).parent / "fixtures"


def _leftovers() -> list[str]:
    return [p.name for p in Path(tempfile.gettempdir()).glob("pre-upload-*")]


def test_sources_page_lists_every_kind(client) -> None:
    response = client.get("/sources")

    assert response.status_code == 200
    for kind in ("financial", "comms", "device", "calendar", "email"):
        assert kind in response.text
    assert "Upload export" in response.text


def test_upload_feeds_the_queue_and_cleans_up(client, session: Session) -> None:
    before = _leftovers()

    response = client.post(
        "/sources/notes",
        files={"file": ("notes.json", (FIXTURES / "notes.json").read_bytes())},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert len(list_pending(session)) >= 2
    assert session.scalars(select(SourceSyncState)).one().tier == "notes"
    assert _leftovers() == before


def test_upload_unknown_kind_404(client) -> None:
    response = client.post(
        "/sources/smoke-detectors",
        files={"file": ("x.json", b"{}")},
        follow_redirects=False,
    )

    assert response.status_code == 404


def test_upload_garbage_reports_readably(client, session: Session) -> None:
    response = client.post(
        "/sources/notes",
        files={"file": ("notes.json", b"not json at all")},
    )

    assert response.status_code == 400
    assert "Could not import" in response.text
    assert list_pending(session) == []


def test_upload_without_file_is_rejected(client) -> None:
    assert client.post("/sources/notes", data={}).status_code == 400


def test_api_upload_json(client, session: Session) -> None:
    response = client.post(
        "/api/sources/social",
        files={"file": ("social.json", (FIXTURES / "social.json").read_bytes())},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "social"
    assert body["proposals_new"] >= 2


def test_api_upload_token_enforced(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRE_API_TOKEN", "secret")

    response = client.post(
        "/api/sources/notes",
        files={"file": ("notes.json", (FIXTURES / "notes.json").read_bytes())},
    )

    assert response.status_code == 401


def test_overview_links_sources_with_count(client, session: Session) -> None:
    client.post(
        "/sources/notes",
        files={"file": ("notes.json", (FIXTURES / "notes.json").read_bytes())},
    )

    response = client.get("/")

    assert response.status_code == 200
    assert "/sources" in response.text
    assert "1/12 connected" in response.text
