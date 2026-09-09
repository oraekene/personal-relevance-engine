"""Ticket 25 commit 1: registry, Google login, provisioning, routing. No live Google."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Self

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pre.db import make_session_factory
from pre.models import Person, Tenant, TenantSession


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


def _google_fake(email: str):
    def _fake(request, timeout: int = 60) -> _Resp:
        url: str = request.full_url
        if "token" in url:
            return _Resp({"access_token": "AT", "expires_in": 3600})
        if "userinfo" in url:
            return _Resp({"email": email})
        raise AssertionError(f"unexpected Google call: {url}")

    return _fake


def _login(tclient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
           email: str = "ada@example.com"):
    from pre.google import new_state

    monkeypatch.setenv("PRE_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("PRE_GOOGLE_CLIENT_SECRET", "shh")
    monkeypatch.setenv("TENANT_DBS_DIR", str(tmp_path / "tenants"))
    monkeypatch.setattr("pre.tenants.urlopen", _google_fake(email))
    nonce = new_state(session, "google_login_state")
    response = tclient.get(
        f"/auth/google/callback?code=x&state={nonce}", follow_redirects=False
    )
    assert response.status_code == 303
    assert "pre_session" in response.cookies
    return response


def test_register_idempotent_and_validated(registry_url: str) -> None:
    from pre.tenants import get_registry, register_tenant

    session = make_session_factory(get_registry(registry_url))()
    try:
        first = register_tenant(session, "Ada@Example.com", "sqlite:///a.db")
        second = register_tenant(session, "ada@example.com", "sqlite:///b.db")

        assert first.id == second.id  # same tenant, existing URL wins
        assert second.db_url == "sqlite:///a.db"
        with pytest.raises(ValueError, match="email is required"):
            register_tenant(session, "  ", "sqlite:///c.db")
    finally:
        session.close()


def test_provision_sqlite_is_stable_and_usable(tmp_path: Path) -> None:
    from pre.models import LifeDimension
    from pre.tenants import get_engine, provision_sqlite_tenant

    url = provision_sqlite_tenant(tmp_path / "tenants", "Ada@Example.com")

    assert url.startswith("sqlite:///")
    assert provision_sqlite_tenant(tmp_path / "tenants", "ada@example.com") == url
    probe = make_session_factory(get_engine(url))()
    try:
        assert probe.query(LifeDimension).count() == 0  # tables exist
    finally:
        probe.close()


def test_session_lifecycle(registry_url: str) -> None:
    from datetime import UTC, datetime, timedelta

    from pre.db import make_session_factory
    from pre.tenants import (
        create_session_token,
        destroy_session,
        get_registry,
        register_tenant,
        resolve_session,
    )

    session = make_session_factory(get_registry(registry_url))()
    try:
        tenant = register_tenant(session, "a@x.co", "sqlite:///a.db")
        token = create_session_token(session, tenant.id)
        assert resolve_session(session, token) is not None
        assert resolve_session(session, token).id == tenant.id
        assert resolve_session(session, "forged") is None
        assert resolve_session(session, "") is None

        row = session.scalars(select(TenantSession)).one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()
        assert resolve_session(session, token) is None

        fresh = create_session_token(session, tenant.id)
        destroy_session(session, fresh)
        assert resolve_session(session, fresh) is None
    finally:
        session.close()


def test_login_provisions_tenant_and_cookie(
    tclient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    registry_url: str,
) -> None:
    from pre.db import make_session_factory
    from pre.tenants import get_registry

    _login(tclient, session, monkeypatch, tmp_path)

    reg = make_session_factory(get_registry(registry_url))()
    try:
        tenant = reg.scalars(select(Tenant)).one()
        assert tenant.email == "ada@example.com"
    finally:
        reg.close()

    from pre.models import LifeDimension
    from pre.tenants import get_engine

    probe = make_session_factory(get_engine(tenant.db_url))()
    try:
        assert probe.query(LifeDimension).count() == 0  # provisioned DB is live
    finally:
        probe.close()


def test_callback_rejects_denied_and_bad_state(tclient) -> None:
    denied = tclient.get("/auth/google/callback?error=access_denied")
    assert denied.status_code == 400
    assert tclient.get("/auth/google/callback?code=x&state=nope").status_code == 400


def test_login_without_registry_redirects_home(client_solo) -> None:
    assert client_solo.get("/auth/google/login", follow_redirects=False).status_code in (303, 400)


@pytest.fixture()
def client_solo(session: Session):
    from fastapi.testclient import TestClient

    from pre.web import create_app

    engine = session.get_bind()

    def factory() -> Session:
        return sessionmaker(bind=engine, expire_on_commit=False)()

    return TestClient(create_app(factory))


def test_routing_isolates_tenants(
    tclient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    registry_url: str,
) -> None:
    from pre.db import make_session_factory
    from pre.tenants import (
        LoginRequired,
        create_session_token,
        get_engine,
        get_registry,
        open_request_session,
        provision_sqlite_tenant,
        register_tenant,
    )

    reg = make_session_factory(get_registry(registry_url))()
    try:
        tenants = {}
        for email in ("a@x.co", "b@x.co"):
            url = provision_sqlite_tenant(tmp_path / "tenants", email)
            tenant = register_tenant(reg, email, url)
            tenants[email] = (tenant, create_session_token(reg, tenant.id))
            person_db = make_session_factory(get_engine(url))()
            try:
                person_db.add(Person(display_name=f"Friend of {email}"))
                person_db.commit()
            finally:
                person_db.close()
    finally:
        reg.close()

    def _names(token: str) -> set[str]:
        factory = make_session_factory(get_registry(registry_url))
        got, _tenant = open_request_session({"pre_session": token}, factory, registry_url)
        try:
            return {p.display_name for p in got.query(Person).all()}
        finally:
            got.close()

    assert _names(tenants["a@x.co"][1]) == {"Friend of a@x.co"}
    assert _names(tenants["b@x.co"][1]) == {"Friend of b@x.co"}

    with pytest.raises(LoginRequired):
        open_request_session({"pre_session": "forged"}, lambda: session, registry_url)


def test_legacy_mode_ignores_registry(
    client_solo, session: Session
) -> None:
    from pre.tenants import open_request_session

    got, tenant = open_request_session({}, lambda: session, None)

    assert tenant is None
    assert got is session


def test_http_gates_and_overview_identity(
    tclient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    assert tclient.get("/api/digest/daily").status_code == 401
    assert tclient.get("/", follow_redirects=False).status_code == 303

    _login(tclient, session, monkeypatch, tmp_path)

    assert tclient.get("/api/digest/daily").status_code == 200
    overview = tclient.get("/")
    assert "Signed in as ada@example.com" in overview.text

    state = tclient.post("/api/interview/physical_health", json={"satisfaction": 6, "goals": []})
    assert state.status_code == 200
    body = tclient.get("/api/interview").json()
    assert body["done"] == 1


def test_logout_clears_session(
    tclient, session: Session, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _login(tclient, session, monkeypatch, tmp_path)
    assert tclient.get("/api/digest/daily").status_code == 200

    assert tclient.post("/auth/logout", follow_redirects=False).status_code == 303
    assert tclient.get("/api/digest/daily").status_code == 401


def test_cli_provision_tenant(tmp_path: Path, registry_url: str) -> None:
    from pre.cli import main
    from pre.db import make_session_factory
    from pre.tenants import get_registry

    assert main([
        "provision-tenant", "--email", "op@example.com",
        "--db-url", "sqlite:///op.db", "--registry", registry_url,
    ]) == 0
    reg = make_session_factory(get_registry(registry_url))()
    try:
        assert reg.scalars(select(Tenant)).one().email == "op@example.com"
    finally:
        reg.close()

    assert main(["provision-tenant", "--email", "x@y.co", "--db-url", "sqlite:///x.db"]) == 1
