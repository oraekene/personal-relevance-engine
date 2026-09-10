"""Ticket 27: per-tenant web push (VAPID) with quiet hours + PWA routes."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Self

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker


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


SUB = {
    "endpoint": "https://push.example/sub-1",
    "keys": {"p256dh": "dh1", "auth": "a1"},
}


def _tenant_db(registry_url: str) -> Session:
    from pre.db import make_session_factory
    from pre.models import Tenant
    from pre.tenants import get_engine, get_registry

    reg = make_session_factory(get_registry(registry_url))()
    try:
        tenant = reg.scalars(select(Tenant)).one()
        return make_session_factory(get_engine(tenant.db_url))()
    finally:
        reg.close()


def _hour(moment_hour: int) -> datetime:
    return datetime(2026, 9, 10, moment_hour, 0, tzinfo=UTC)


# --- unit: quiet hours -------------------------------------------------------


def test_quiet_defaults_and_window(session: Session) -> None:
    from pre.push import get_quiet_hours, in_quiet_hours

    assert get_quiet_hours(session) == (22, 7)
    assert in_quiet_hours(session, _hour(23)) is True
    assert in_quiet_hours(session, _hour(3)) is True
    assert in_quiet_hours(session, _hour(12)) is False
    assert in_quiet_hours(session, _hour(7)) is False
    assert in_quiet_hours(session, _hour(22)) is True


def test_quiet_roundtrip_and_validation(session: Session) -> None:
    from pre.push import get_quiet_hours, in_quiet_hours, set_quiet_hours

    set_quiet_hours(session, 1, 6)
    assert get_quiet_hours(session) == (1, 6)
    assert in_quiet_hours(session, _hour(3)) is True
    assert in_quiet_hours(session, _hour(12)) is False
    with pytest.raises(ValueError, match="hour"):
        set_quiet_hours(session, 25, 6)


# --- unit: subscriptions -----------------------------------------------------


def test_subscriptions_roundtrip(session: Session) -> None:
    from pre.push import add_subscription, list_subscriptions, remove_subscription

    assert list_subscriptions(session) == []
    add_subscription(session, SUB["endpoint"], SUB["keys"])
    add_subscription(session, SUB["endpoint"], SUB["keys"])  # idempotent
    assert [s.endpoint for s in list_subscriptions(session)] == [SUB["endpoint"]]
    remove_subscription(session, SUB["endpoint"])
    assert list_subscriptions(session) == []


def test_vapid_keygen_makes_pair() -> None:
    from pre.push import generate_vapid_keys

    first, second = generate_vapid_keys(), generate_vapid_keys()
    assert first["public"] and first["private"] and first["public"] != first["private"]
    assert first["public"] != second["public"]


# --- unit: notify ------------------------------------------------------------


def _seed_undecided(db: Session) -> None:
    from pre.models import Change, DigestItem

    change = Change(product_name="P", title="T", change_type="feature", fingerprint="fp-1")
    db.add(change)
    db.commit()
    db.add(
        DigestItem(
            digest_kind="daily", change_id=change.id, score=80,
            entity_type="tool", entity_id=1, entity_label="P",
        )
    )
    db.commit()


class _Sender:
    def __init__(self, dead: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.dead = dead

    def __call__(self, subscription: object, payload: dict[str, object]) -> None:
        from pywebpush import WebPushException

        self.calls.append({"subscription": subscription, "payload": payload})
        if self.dead:
            raise WebPushException("gone", response=type("R", (), {"status_code": 410})())


def test_notify_sends_and_dedupes(session: Session) -> None:
    from pre.push import add_subscription, notify_new_digest

    _seed_undecided(session)
    add_subscription(session, SUB["endpoint"], SUB["keys"])
    sender = _Sender()
    first = notify_new_digest(session, "https://app.example", sender=sender)
    assert first["sent"] == 1
    assert first["undecided"] == 1
    assert sender.calls[0]["payload"]["url"] == "https://app.example/digest/daily"
    again = notify_new_digest(session, "https://app.example", sender=sender)
    assert again["sent"] == 0  # nothing new since the last notify


def test_notify_respects_quiet_hours(session: Session) -> None:
    from pre.push import add_subscription, notify_new_digest, set_quiet_hours

    _seed_undecided(session)
    add_subscription(session, SUB["endpoint"], SUB["keys"])
    set_quiet_hours(session, 0, 23)  # quiet almost all day
    sender = _Sender()
    result = notify_new_digest(session, "https://app.example", sender=sender,
                               now=_hour(12))
    assert result["sent"] == 0
    assert result["skipped_quiet"] is True
    assert sender.calls == []


def test_notify_prunes_dead_subscriptions(session: Session) -> None:
    from pre.push import add_subscription, list_subscriptions, notify_new_digest

    _seed_undecided(session)
    add_subscription(session, SUB["endpoint"], SUB["keys"])
    result = notify_new_digest(session, "https://app.example", sender=_Sender(dead=True))
    assert result["sent"] == 0
    assert result["pruned"] == 1
    assert list_subscriptions(session) == []


def test_notify_silent_without_subscriptions(session: Session) -> None:
    from pre.push import notify_new_digest

    _seed_undecided(session)
    assert notify_new_digest(session, "https://app.example", sender=_Sender())["sent"] == 0


# --- API ---------------------------------------------------------------------


def test_push_requires_login(tclient) -> None:
    assert tclient.post("/api/push/subscribe", json=SUB).status_code == 401
    assert tclient.get("/api/push/status").status_code == 401


def test_push_subscribe_roundtrip(
    tclient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _login(tclient, session, monkeypatch, tmp_path)
    assert tclient.post("/api/push/subscribe", json=SUB).status_code == 200
    status = tclient.get("/api/push/status").json()
    assert status["subscriptions"] == [SUB["endpoint"]]
    assert tclient.post("/api/push/unsubscribe", json=SUB).status_code == 200
    assert tclient.get("/api/push/status").json()["subscriptions"] == []


def test_push_quiet_roundtrip(
    tclient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _login(tclient, session, monkeypatch, tmp_path)
    assert tclient.get("/api/push/quiet").json() == {"start_hour": 22, "end_hour": 7}
    updated = tclient.post("/api/push/quiet", json={"start_hour": 1, "end_hour": 6})
    assert updated.json() == {"start_hour": 1, "end_hour": 6}
    assert tclient.post("/api/push/quiet", json={"start_hour": 99, "end_hour": 6}).status_code == 400


def test_push_vapid_key(tclient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert tclient.get("/api/push/vapid-public-key").json() == {"public_key": ""}
    monkeypatch.setenv("PRE_VAPID_PUBLIC_KEY", "test-public")
    assert tclient.get("/api/push/vapid-public-key").json() == {"public_key": "test-public"}


def test_pwa_routes_are_public(tclient, monkeypatch: pytest.MonkeyPatch) -> None:
    manifest = tclient.get("/manifest.webmanifest")
    assert manifest.status_code == 200
    assert manifest.json()["start_url"] == "/"
    worker = tclient.get("/sw.js")
    assert worker.status_code == 200
    assert "push" in worker.text
    assert tclient.get("/.well-known/assetlinks.json").json() == []
    monkeypatch.setenv("PRE_ANDROID_PACKAGE", "com.example.pre")
    monkeypatch.setenv("PRE_ASSETLINKS_SHA256", "AA:BB")
    links = tclient.get("/.well-known/assetlinks.json").json()
    assert links[0]["target"]["package_name"] == "com.example.pre"


def test_cli_notify_dry_run(
    registry_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pre.cli import main
    from pre.db import make_session_factory
    from pre.push import add_subscription
    from pre.tenants import get_engine, get_registry, provision_sqlite_tenant, register_tenant

    url = provision_sqlite_tenant(tmp_path / "tenants", "a@x.co")
    reg = make_session_factory(get_registry(registry_url))()
    try:
        register_tenant(reg, "a@x.co", url)
    finally:
        reg.close()
    db = make_session_factory(get_engine(url))()
    try:
        _seed_undecided(db)
        add_subscription(db, SUB["endpoint"], SUB["keys"])
    finally:
        db.close()

    assert main(["notify", "--registry", registry_url, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "1 undecided" in out and "1 subscriptions" in out
    assert main(["notify"]) == 1


def test_cli_vapid_keygen(capsys: pytest.CaptureFixture[str]) -> None:
    from pre.cli import main

    assert main(["vapid-keygen"]) == 0
    assert "PRE_VAPID_PUBLIC_KEY=" in capsys.readouterr().out
