"""Ticket 26: browser-capture lane (consent-gated) + capture/overlay API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Self

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pre.models import Change


@pytest.fixture()
def registry_url(tmp_path: Path):
    from pre.tenants import clear_engine_cache

    url = f"sqlite:///{tmp_path / 'registry.db'}"
    yield url
    clear_engine_cache()


@pytest.fixture()
def tclient(session: Session, registry_url: str):
    from fastapi.testclient import TestClient

    from pre.web import create_app

    engine = session.get_bind()

    def factory() -> Session:
        return sessionmaker(bind=engine, expire_on_commit=False)()

    return TestClient(create_app(factory, tenant_registry_url=registry_url))


class _Resp:
    def __init__(self, payload: object) -> None:
        self._data = json.dumps(payload).encode()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._data


def _login(tclient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
           email: str = "ada@example.com"):
    from pre.google import new_state

    def _fake(request, timeout: int = 60) -> _Resp:
        url: str = request.full_url
        if "token" in url:
            return _Resp({"access_token": "AT", "expires_in": 3600})
        if "userinfo" in url:
            return _Resp({"email": email})
        raise AssertionError(f"unexpected Google call: {url}")

    monkeypatch.setenv("PRE_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("PRE_GOOGLE_CLIENT_SECRET", "shh")
    monkeypatch.setenv("TENANT_DBS_DIR", str(tmp_path / "tenants"))
    monkeypatch.setattr("pre.tenants.urlopen", _fake)
    nonce = new_state(session, "google_login_state")
    response = tclient.get(f"/auth/google/callback?code=x&state={nonce}", follow_redirects=False)
    assert response.status_code == 303
    return response


def _enable(tclient) -> None:
    response = tclient.post("/api/capture/consent", json={"enabled": True, "blocked_hosts": []})
    assert response.status_code == 200


# --- unit: URL shaping -------------------------------------------------------


def test_product_for_url() -> None:
    from pre.extension import product_for_url

    assert product_for_url("https://support.google.com/a/b") == "google"
    assert product_for_url("https://www.example.com/pricing") == "example"
    assert product_for_url("not a url") == ""
    assert product_for_url("ftp://files.example.com/x") == ""


def test_normalize_url() -> None:
    from pre.extension import normalize_url

    assert normalize_url("https://Example.com/P/?x=1#frag") == "https://example.com/P?x=1"
    assert normalize_url("http://example.com/") == "http://example.com"
    assert normalize_url("notaurl") == ""


def test_consent_defaults_off(session: Session) -> None:
    from pre.extension import get_consent

    assert get_consent(session) == (False, set())


def test_consent_roundtrip(session: Session) -> None:
    from pre.extension import get_consent, set_consent

    set_consent(session, True, {"evil.example"})
    assert get_consent(session) == (True, {"evil.example"})
    set_consent(session, False, set())
    assert get_consent(session) == (False, set())


# --- unit: capture rules -----------------------------------------------------


def test_record_capture_refused_when_disabled(session: Session) -> None:
    from pre.extension import record_capture

    with pytest.raises(PermissionError, match="disabled"):
        record_capture(session, "https://example.com/p", "Example pricing")


def test_record_capture_rejects_bad_input(session: Session) -> None:
    from pre.extension import record_capture, set_consent

    set_consent(session, True, set())
    with pytest.raises(ValueError, match="url"):
        record_capture(session, "notaurl", "Some title")
    with pytest.raises(ValueError, match="title"):
        record_capture(session, "https://example.com/p", "  ")


def test_record_capture_refuses_app_host(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pre.extension import record_capture, set_consent

    monkeypatch.setenv("PRE_PUBLIC_URL", "https://app.example")
    set_consent(session, True, set())
    with pytest.raises(ValueError, match="app itself"):
        record_capture(session, "https://app.example/digest/daily", "Digest page")


def test_record_capture_creates_then_dedupes(session: Session) -> None:
    from pre.extension import record_capture, set_consent

    set_consent(session, True, set())
    first = record_capture(session, "https://example.com/pricing", "Example pricing update")
    assert first.outcome == "created"
    assert first.product == "example"
    second = record_capture(session, "https://example.com/pricing#plans", "Example pricing update")
    assert second.outcome == "deduped"
    assert len(session.scalars(select(Change)).all()) == 1


def test_record_capture_strengthens_known_change(session: Session) -> None:
    from pre.change_corpus import FirehoseEntry, fingerprint_for, ingest_entries
    from pre.extension import record_capture, set_consent

    ingest_entries(
        session,
        [FirehoseEntry(product_name="example", title="Example pricing update",
                        url="https://agg.example/feed")],
        "firehose",
    )
    set_consent(session, True, set())
    outcome = record_capture(session, "https://example.com/pricing", "Example pricing update")
    assert outcome.outcome == "strengthened"
    row = session.scalars(select(Change)).one()
    assert {s["source"] for s in row.sources_json} == {"firehose", "browser-capture"}
    assert fingerprint_for("example", "Example pricing update") == row.fingerprint


def test_record_capture_respects_blocklist(session: Session) -> None:
    from pre.extension import record_capture, set_consent

    set_consent(session, True, {"example.com"})
    with pytest.raises(PermissionError, match="blocked"):
        record_capture(session, "https://example.com/p", "Example page")


# --- API ---------------------------------------------------------------------


def test_capture_requires_login(tclient) -> None:
    assert tclient.post("/api/capture", json={"url": "https://e.com/p", "title": "T"}).status_code == 401
    assert tclient.get("/api/capture/status").status_code == 401
    assert tclient.get("/api/overlay?url=https://e.com/p").status_code == 401


def test_cookie_authenticates_api_when_bearer_configured(
    tclient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _login(tclient, session, monkeypatch, tmp_path)
    monkeypatch.setenv("PRE_API_TOKEN", "server-token")
    assert tclient.get("/api/digest/daily").status_code == 200
    assert tclient.get("/api/capture/status").status_code == 200


def test_bearer_without_cookie_stays_rejected(
    tclient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRE_API_TOKEN", "server-token")
    assert tclient.get("/api/digest/daily").status_code == 401
    denied = tclient.get(
        "/api/digest/daily", headers={"authorization": "Bearer server-token"}
    )
    assert denied.status_code == 401


def test_capture_consent_gate(
    tclient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _login(tclient, session, monkeypatch, tmp_path)
    assert tclient.get("/api/capture/status").json() == {"enabled": False, "blocked_hosts": []}
    blocked = tclient.post(
        "/api/capture", json={"url": "https://example.com/p", "title": "Example page"}
    )
    assert blocked.status_code == 403

    _enable(tclient)
    assert tclient.get("/api/capture/status").json()["enabled"] is True
    created = tclient.post(
        "/api/capture", json={"url": "https://example.com/p", "title": "Example page"}
    )
    assert created.status_code == 200
    assert created.json()["outcome"] == "created"
    repeat = tclient.post(
        "/api/capture", json={"url": "https://example.com/p", "title": "Example page"}
    )
    assert repeat.json()["outcome"] == "deduped"


def test_capture_blocked_host(
    tclient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _login(tclient, session, monkeypatch, tmp_path)
    response = tclient.post(
        "/api/capture/consent", json={"enabled": True, "blocked_hosts": ["example.com"]}
    )
    assert response.status_code == 200
    blocked = tclient.post(
        "/api/capture", json={"url": "https://example.com/p", "title": "Example page"}
    )
    assert blocked.status_code == 403


def test_consent_toggle_preserves_blocklist(
    tclient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _login(tclient, session, monkeypatch, tmp_path)
    tclient.post(
        "/api/capture/consent", json={"enabled": True, "blocked_hosts": ["example.com"]}
    )
    toggled = tclient.post("/api/capture/consent", json={"enabled": True})
    assert toggled.json() == {"enabled": True, "blocked_hosts": ["example.com"]}
    replaced = tclient.post("/api/capture/consent", json={"enabled": True, "blocked_hosts": []})
    assert replaced.json() == {"enabled": True, "blocked_hosts": []}


def test_overlay_matches_visited_page(
    tclient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    registry_url: str,
) -> None:
    from pre.db import make_session_factory
    from pre.models import DigestItem, Tenant
    from pre.tenants import get_engine, get_registry

    _login(tclient, session, monkeypatch, tmp_path)
    _enable(tclient)
    tclient.post(
        "/api/capture",
        json={"url": "https://example.com/pricing", "title": "Example pricing update"},
    )
    reg = make_session_factory(get_registry(registry_url))()
    try:
        tenant = reg.scalars(select(Tenant)).one()
        db = make_session_factory(get_engine(tenant.db_url))()
        try:
            change = db.scalars(select(Change)).one()
            item = DigestItem(
                digest_kind="daily", change_id=change.id, score=80,
                entity_type="tool", entity_id=1, entity_label="Example",
            )
            db.add(item)
            db.commit()
            item_id = item.id
        finally:
            db.close()
    finally:
        reg.close()

    response = tclient.get("/api/overlay", params={"url": "https://example.com/pricing?x=1"})
    assert response.status_code == 200
    matches = response.json()["matches"]
    assert len(matches) == 1
    assert matches[0]["item_id"] == item_id
    assert matches[0]["product"] == "example"

    other = tclient.get("/api/overlay", params={"url": "https://other.example/page"})
    assert other.json()["matches"] == []

    elsewhere = tclient.get("/api/overlay", params={"url": "https://example.com/careers"})
    assert elsewhere.json()["matches"] == []
