"""Ticket 25 commits 1-2: registry, login, routing, caps, operator rollup."""

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


def test_cli_provision_tenant_cap_override(tmp_path: Path, registry_url: str) -> None:
    from pre.cli import main
    from pre.db import make_session_factory
    from pre.tenants import effective_cap_cents, get_registry

    assert main([
        "provision-tenant", "--email", "cap@example.com",
        "--db-url", "sqlite:///cap.db", "--registry", registry_url,
        "--cap-override-cents", "500",
    ]) == 0
    reg = make_session_factory(get_registry(registry_url))()
    try:
        tenant = reg.scalars(select(Tenant)).one()
        assert tenant.cap_override_cents == 500
        assert effective_cap_cents(tenant) == 500
    finally:
        reg.close()

    # Re-provisioning the same email updates the cap but never repoints the DB.
    assert main([
        "provision-tenant", "--email", "cap@example.com",
        "--db-url", "sqlite:///other.db", "--registry", registry_url,
        "--cap-override-cents", "900",
    ]) == 0
    reg = make_session_factory(get_registry(registry_url))()
    try:
        tenant = reg.scalars(select(Tenant)).one()
        assert tenant.db_url == "sqlite:///cap.db"
        assert tenant.cap_override_cents == 900
    finally:
        reg.close()


def test_effective_cap_prefers_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from pre.cost_meter import DEFAULT_MONTHLY_CAP_CENTS
    from pre.models import Tenant
    from pre.tenants import effective_cap_cents

    monkeypatch.setenv("PRE_MONTHLY_CAP_CENTS", "7777")

    assert effective_cap_cents(None) == 7777
    plain = Tenant(email="a@x.co", db_url="sqlite:///a.db", cap_override_cents=None)
    assert effective_cap_cents(plain) == 7777
    boosted = Tenant(email="b@x.co", db_url="sqlite:///b.db", cap_override_cents=500)
    assert effective_cap_cents(boosted) == 500
    assert DEFAULT_MONTHLY_CAP_CENTS == 2000


def _seed_spend(url: str, cents: float, backup: bool = False) -> None:
    from pre.cost_meter import CallRecord, log_call
    from pre.ops import mark_backup
    from pre.tenants import get_engine

    factory = make_session_factory(get_engine(url))
    db = factory()
    try:
        log_call(
            db,
            CallRecord(
                purpose="judge", model="gpt-4o-mini",
                prompt_tokens=0, completion_tokens=0, cost_usd_cents=cents,
            ),
        )
        if backup:
            mark_backup(db)
    finally:
        db.close()


def test_operator_rollup_reports_spend_and_caps(
    tclient, registry_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pre.tenants import get_registry, provision_sqlite_tenant, register_tenant

    monkeypatch.setenv("PRE_OPERATOR_TOKEN", "op-secret")
    monkeypatch.setenv("PRE_MONTHLY_CAP_CENTS", "7777")
    reg = make_session_factory(get_registry(registry_url))()
    try:
        url_a = provision_sqlite_tenant(tmp_path / "tenants", "a@x.co")
        url_b = provision_sqlite_tenant(tmp_path / "tenants", "b@x.co")
        tenant_a = register_tenant(reg, "a@x.co", url_a)
        register_tenant(reg, "b@x.co", url_b)
        tenant_a.cap_override_cents = 500
        reg.commit()
    finally:
        reg.close()
    _seed_spend(url_a, 100.0, backup=True)
    _seed_spend(url_b, 25.0)

    denied = tclient.get("/api/ops/tenants")
    assert denied.status_code == 403
    wrong = tclient.get("/api/ops/tenants", headers={"authorization": "Bearer nope"})
    assert wrong.status_code == 403
    monkeypatch.setenv("PRE_API_TOKEN", "tenant-token")
    cross = tclient.get("/api/ops/tenants", headers={"authorization": "Bearer tenant-token"})
    assert cross.status_code == 403

    response = tclient.get("/api/ops/tenants", headers={"authorization": "Bearer op-secret"})
    assert response.status_code == 200
    rows = {row["email"]: row for row in response.json()["tenants"]}
    assert rows["a@x.co"]["spent_cents"] == 100.0
    assert rows["a@x.co"]["cap_cents"] == 500
    assert rows["a@x.co"]["last_backup"] is not None
    assert rows["b@x.co"]["spent_cents"] == 25.0
    assert rows["b@x.co"]["cap_cents"] == 7777
    assert rows["b@x.co"]["last_backup"] is None


def test_operator_rollup_rejects_single_tenant_mode(
    client_solo, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert client_solo.get("/api/ops/tenants").status_code == 403
    monkeypatch.setenv("PRE_OPERATOR_TOKEN", "op-secret")
    response = client_solo.get("/api/ops/tenants", headers={"authorization": "Bearer op-secret"})
    assert response.status_code == 400


def test_cap_for_session_prefers_override(
    registry_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pre.tenants import cap_for_session, get_engine, get_registry, provision_sqlite_tenant

    url = provision_sqlite_tenant(tmp_path / "tenants", "a@x.co")
    probe = make_session_factory(get_engine(url))()
    try:
        assert cap_for_session(probe) == 2000  # no registry: legacy global default
    finally:
        probe.close()

    from pre.tenants import register_tenant

    monkeypatch.setenv("TENANT_REGISTRY_URL", registry_url)
    reg = make_session_factory(get_registry(registry_url))()
    try:
        tenant = register_tenant(reg, "a@x.co", url)
        tenant.cap_override_cents = 500
        reg.commit()
    finally:
        reg.close()

    probe = make_session_factory(get_engine(url))()
    try:
        assert cap_for_session(probe) == 500
    finally:
        probe.close()


def test_judge_enforces_tenant_override(
    registry_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pre.cost_meter import BudgetExceeded, CallRecord, log_call
    from pre.judge import LLMJudge
    from pre.models import Change
    from pre.tenants import get_engine, get_registry, provision_sqlite_tenant, register_tenant

    monkeypatch.setenv("PRE_MONTHLY_CAP_CENTS", "100")
    monkeypatch.setenv("TENANT_REGISTRY_URL", registry_url)
    monkeypatch.setenv("PRE_LLM_API_KEY", "test")
    url = provision_sqlite_tenant(tmp_path / "tenants", "a@x.co")
    reg = make_session_factory(get_registry(registry_url))()
    try:
        tenant = register_tenant(reg, "a@x.co", url)
        tenant.cap_override_cents = 10000
        reg.commit()
    finally:
        reg.close()

    db = make_session_factory(get_engine(url))()
    try:
        log_call(
            db,
            CallRecord(
                purpose="judge", model="gpt-4o-mini",
                prompt_tokens=0, completion_tokens=0, cost_usd_cents=150.0,
            ),
        )
        change = Change(
            product_name="P", title="T", change_type="feature", fingerprint="fp-1"
        )
        db.add(change)
        db.commit()

        judge = LLMJudge(api_key="test")
        monkeypatch.setattr(judge, "_complete", lambda prompt: ('{"verdicts": []}', 10, 5))
        judge.score(db, change, [])  # 150 spent: over global 100, under override

        reg_session = make_session_factory(get_registry(registry_url))()
        try:
            row = reg_session.scalars(select(Tenant)).one()
            row.cap_override_cents = 120
            reg_session.commit()
        finally:
            reg_session.close()
        with pytest.raises(BudgetExceeded):
            judge.score(db, change, [])
    finally:
        db.close()


def test_costs_command_shows_override(
    registry_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from pre.cli import main
    from pre.tenants import get_registry, provision_sqlite_tenant, register_tenant

    monkeypatch.setenv("TENANT_REGISTRY_URL", registry_url)
    url = provision_sqlite_tenant(tmp_path / "tenants", "a@x.co")
    reg = make_session_factory(get_registry(registry_url))()
    try:
        tenant = register_tenant(reg, "a@x.co", url)
        tenant.cap_override_cents = 500
        reg.commit()
    finally:
        reg.close()

    assert main(["costs", "--db", url]) == 0
    assert "/ 500 cents" in capsys.readouterr().out
