"""Ticket 25 commit 3: permanent cross-tenant isolation suite.

Three classes on real multi-file SQLite DBs: wrong-tenant requests fail
closed, rows never mix between tenant databases, and the registry protects
itself (opaque sessions, Profile-free schema, distinct files).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session, sessionmaker

from pre.db import make_session_factory
from pre.models import Person, RegistryBase, TenantSession


@pytest.fixture()
def registry_url(tmp_path: Path):
    from pre.tenants import clear_engine_cache

    url = f"sqlite:///{tmp_path / 'registry.db'}"
    yield url
    clear_engine_cache()


@pytest.fixture()
def tenant_client(session: Session, registry_url: str):
    from fastapi.testclient import TestClient

    from pre.web import create_app

    engine = session.get_bind()

    def factory() -> Session:
        return sessionmaker(bind=engine, expire_on_commit=False)()

    return TestClient(create_app(factory, tenant_registry_url=registry_url))


def _provision_two(tmp_path: Path, registry_url: str) -> dict[str, dict[str, str]]:
    from pre.tenants import (
        create_session_token,
        get_engine,
        get_registry,
        provision_sqlite_tenant,
        register_tenant,
    )

    world: dict[str, dict[str, str]] = {}
    reg = make_session_factory(get_registry(registry_url))()
    try:
        for email in ("a@x.co", "b@x.co"):
            url = provision_sqlite_tenant(tmp_path / "tenants", email)
            tenant = register_tenant(reg, email, url)
            token = create_session_token(reg, tenant.id)
            world[email] = {"url": url, "token": token}
            db = make_session_factory(get_engine(url))()
            try:
                db.add(Person(display_name=f"Friend of {email}"))
                db.commit()
            finally:
                db.close()
    finally:
        reg.close()
    return world


class TestWrongTenantFailsClosed:
    def test_forged_cookie_rejected(self, tenant_client, registry_url: str) -> None:
        assert tenant_client.get("/api/digest/daily").status_code == 401
        home = tenant_client.get("/", follow_redirects=False)
        assert home.status_code == 303

    def test_expired_session_rejected(
        self, tenant_client, registry_url: str, tmp_path: Path
    ) -> None:
        from datetime import UTC, datetime, timedelta

        from pre.tenants import get_registry

        world = _provision_two(tmp_path, registry_url)
        reg = make_session_factory(get_registry(registry_url))()
        try:
            row = reg.scalars(select(TenantSession)).first()
            assert row is not None
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            reg.commit()
        finally:
            reg.close()

        token = world["a@x.co"]["token"]
        tenant_client.cookies.set("pre_session", token)
        assert tenant_client.get("/api/digest/daily").status_code == 401

    def test_revoked_session_rejected(
        self, tenant_client, registry_url: str, tmp_path: Path
    ) -> None:
        from pre.tenants import destroy_session, get_registry

        world = _provision_two(tmp_path, registry_url)
        token = world["a@x.co"]["token"]
        reg = make_session_factory(get_registry(registry_url))()
        try:
            destroy_session(reg, token)
        finally:
            reg.close()

        tenant_client.cookies.set("pre_session", token)
        assert tenant_client.get("/api/digest/daily").status_code == 401


class TestRowsNeverMix:
    def test_profile_and_spend_stay_home(self, registry_url: str, tmp_path: Path) -> None:
        from pre.cost_meter import CallRecord, log_call, month_to_date_cents
        from pre.tenants import get_engine, get_registry, open_request_session

        world = _provision_two(tmp_path, registry_url)
        for email, cents in (("a@x.co", 100.0), ("b@x.co", 25.0)):
            db = make_session_factory(get_engine(world[email]["url"]))()
            try:
                log_call(
                    db,
                    CallRecord(
                        purpose="judge", model="gpt-4o-mini",
                        prompt_tokens=0, completion_tokens=0, cost_usd_cents=cents,
                    ),
                )
            finally:
                db.close()

        factory = make_session_factory(get_registry(registry_url))
        for email, cents in (("a@x.co", 100.0), ("b@x.co", 25.0)):
            got, _tenant = open_request_session(
                {"pre_session": world[email]["token"]}, factory, registry_url
            )
            try:
                assert {p.display_name for p in got.query(Person).all()} == {f"Friend of {email}"}
                assert month_to_date_cents(got) == cents
            finally:
                got.close()


class TestRegistrySelfProtects:
    def test_session_tokens_stored_hashed(self, registry_url: str) -> None:
        from datetime import UTC, datetime

        from pre.tenants import (
            SESSION_TTL_DAYS,
            create_session_token,
            get_registry,
            register_tenant,
        )

        reg = make_session_factory(get_registry(registry_url))()
        try:
            tenant = register_tenant(reg, "a@x.co", "sqlite:///a.db")
            token = create_session_token(reg, tenant.id)
            hashes = set(reg.scalars(select(TenantSession.token_hash)).all())
            assert token not in hashes  # plaintext never rests
            assert hashlib.sha256(token.encode()).hexdigest() in hashes
            row = reg.scalars(select(TenantSession)).one()
            ttl = (row.expires_at - datetime.now(UTC).replace(tzinfo=None)).days
            assert SESSION_TTL_DAYS - 1 <= ttl <= SESSION_TTL_DAYS
        finally:
            reg.close()

    def test_registry_schema_has_no_profile_tables(self, registry_url: str, tmp_path: Path) -> None:
        from pre.db import Base
        from pre.tenants import get_engine, get_registry, provision_sqlite_tenant

        assert set(RegistryBase.metadata.tables) == {"tenants", "tenant_sessions"}
        assert "people" in Base.metadata.tables  # profile tables exist, just elsewhere

        reg_tables = set(inspect(get_registry(registry_url)).get_table_names())
        assert reg_tables == {"tenants", "tenant_sessions"}

        tenant_url = provision_sqlite_tenant(tmp_path / "probe", "probe@x.co")
        tenant_tables = set(inspect(get_engine(tenant_url)).get_table_names())
        assert "people" in tenant_tables and "tenants" not in tenant_tables

    def test_tenant_databases_are_distinct_files(self, registry_url: str, tmp_path: Path) -> None:
        from pre.tenants import get_engine, provision_sqlite_tenant

        base = tmp_path / "tenants"
        url_a = provision_sqlite_tenant(base, "a@x.co")
        url_b = provision_sqlite_tenant(base, "b@x.co")
        assert url_a != url_b
        for url in (url_a, url_b):
            path = Path(url.removeprefix("sqlite:///"))
            assert path.parent == base.resolve()
            assert path.as_posix() not in registry_url
        assert get_engine(url_a) is not get_engine(url_b)
