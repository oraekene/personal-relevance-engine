"""Ticket 24 commit 3: MCP tools, mount, and host docs conformance.

In-process client calls exercise the real server object (registration, shapes,
consent gating, verdict writes). HTTP-layer auth is covered by the verifier
unit tests plus the mounted 401 probe; the full OAuth HTTP round-trip lives
in test_mcp_oauth.py.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from sqlalchemy.orm import Session, sessionmaker

from pre.change_corpus import FirehoseEntry, ingest_entries
from pre.digest import assemble_digest
from pre.intake import apply_intake_dict
from pre.judge import ScriptedJudge
from pre.mcp_oauth import set_mcp_consent
from pre.models import Change, DigestItem, NetworkLink, Tool, VerdictLog
from pre.retrieval import index_all
from pre.scoring import judge_change


@pytest.fixture()
def mcp_server(session: Session):
    from pre.mcp_server import create_mcp_server

    engine = session.get_bind()

    def factory() -> Session:
        return sessionmaker(bind=engine, expire_on_commit=False)()

    return create_mcp_server(factory)


@pytest.fixture()
def client(session: Session):
    from fastapi.testclient import TestClient

    from pre.web import create_app

    engine = session.get_bind()

    def factory() -> Session:
        return sessionmaker(bind=engine, expire_on_commit=False)()

    return TestClient(create_app(factory))


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


def _text_of(result: object) -> str:
    return json.dumps(result, default=str)


def test_lists_three_documented_tools(mcp_server) -> None:
    async def _go():
        return await mcp_server.list_tools()

    tools = asyncio.run(_go())
    by_name = {t.name: t for t in tools}

    assert {"get_digest", "record_verdict", "query_profile"} <= set(by_name)
    assert all((t.description or "").strip() for t in by_name.values())


def test_get_digest_returns_items(mcp_server, session: Session) -> None:
    _seeded_item(session)

    async def _go():
        return await mcp_server.call_tool("get_digest", {"kind": "daily"})

    result = asyncio.run(_go())

    assert result.is_error is False
    assert "Apollo" in _text_of(result)


def test_get_digest_rejects_unknown_kind(mcp_server) -> None:
    async def _go():
        return await mcp_server.call_tool("get_digest", {"kind": "hourly"})

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(_go())
    assert "unknown digest kind" in str(exc_info.value.__cause__)


def test_record_verdict_roundtrip(mcp_server, session: Session) -> None:
    item = _seeded_item(session)

    async def _go():
        return await mcp_server.call_tool(
            "record_verdict", {"item_id": item.id, "choice": "dismiss"}
        )

    result = asyncio.run(_go())

    assert result.is_error is False
    session.expire_all()
    assert session.get(DigestItem, item.id).verdict == "dismiss"  # type: ignore[union-attr]
    assert session.query(VerdictLog).one().channel == "mcp"


def test_query_profile_refused_when_disabled(mcp_server, session: Session) -> None:
    _seeded_item(session)

    async def _go():
        return await mcp_server.call_tool("query_profile", {})

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(_go())
    assert "disabled" in str(exc_info.value.__cause__)


def test_query_profile_respects_scope_and_labels(mcp_server, session: Session) -> None:
    _seeded_item(session)
    set_mcp_consent(session, True, {"business"})

    async def _go_full():
        return await mcp_server.call_tool("query_profile", {})

    full = _text_of(asyncio.run(_go_full()))
    assert "Use Apollo heavily" in full
    assert "user-profile" in full
    assert "third-party-change" in full

    async def _go_narrow():
        return await mcp_server.call_tool("query_profile", {"dimension": "family"})

    with pytest.raises(ToolError) as exc_info:
        asyncio.run(_go_narrow())
    assert "not allowed" in str(exc_info.value.__cause__)

    async def _go_bogus():
        return await mcp_server.call_tool("query_profile", {"dimension": "narnia"})

    with pytest.raises(ToolError) as exc_info2:
        asyncio.run(_go_bogus())
    assert "unknown dimension" in str(exc_info2.value.__cause__)


def test_empty_scope_yields_no_profile_data(mcp_server, session: Session) -> None:
    from pre.mcp_oauth import set_mcp_consent
    from pre.mcp_server import profile_answer

    _seeded_item(session)
    set_mcp_consent(session, True, set())

    answer = profile_answer(session, set(), None)

    assert answer["dimensions"] == []
    assert answer["network"] == []
    assert answer["recent_digest"] == []


def test_mcp_mounted(session: Session) -> None:
    from pre.web import create_app

    engine = session.get_bind()
    app = create_app(lambda: sessionmaker(bind=engine, expire_on_commit=False)())

    assert any(getattr(r, "path", "") == "/mcp" for r in app.routes)


def test_mcp_http_requires_auth(client) -> None:
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "probe", "version": "1"},
            },
        },
        headers={"Accept": "application/json, text/event-stream"},
    )

    assert response.status_code in (401, 403)


def test_openapi_covers_habit_routes(client) -> None:
    paths = client.get("/openapi.json").json()["paths"]

    for route in (
        "/api/digest/{kind}",
        "/api/verdict",
        "/api/matrix",
        "/api/interview",
        "/api/settings",
        "/api/go-live",
    ):
        assert route in paths, route


# --- tenant binding (issue 25) ------------------------------------------------------


@pytest.fixture()
def tenant_world(tmp_path):
    from pre.db import make_session_factory
    from pre.models import Person
    from pre.tenants import (
        clear_engine_cache,
        get_engine,
        get_registry,
        provision_sqlite_tenant,
        register_tenant,
    )

    reg_url = f"sqlite:///{tmp_path / 'registry.db'}"
    reg = make_session_factory(get_registry(reg_url))()
    world = {"registry": reg_url, "tenants": {}}
    try:
        for email in ("a@x.co", "b@x.co"):
            url = provision_sqlite_tenant(tmp_path / "tenants", email)
            tenant = register_tenant(reg, email, url)
            world["tenants"][email] = {"id": tenant.id, "url": url}
            person_db = make_session_factory(get_engine(url))()
            try:
                friend = Person(display_name=f"Friend of {email}")
                person_db.add(friend)
                person_db.flush()
                person_db.add(NetworkLink(person_id=friend.id))
                person_db.commit()
            finally:
                person_db.close()
    finally:
        reg.close()
    yield world
    clear_engine_cache()


VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # RFC 7636 vector


def test_tenant_grant_carries_db_url(
    session: Session, tenant_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from pre.mcp_oauth import (
        VaultVerifier,
        approve_authorize,
        exchange_authorization_code,
        register_client,
    )

    monkeypatch.setenv("PRE_API_TOKEN", "sekret")
    url = tenant_world["tenants"]["a@x.co"]["url"]
    reg = register_client(session, ["http://localhost:9/cb"], "t", "digest:read")
    code = approve_authorize(
        session, reg["client_id"], url, "http://localhost:9/cb",
        ["digest:read"], CHALLENGE, "sekret", False, set(),
    )
    payload = exchange_authorization_code(
        session, reg["client_id"], reg["client_secret"], code, "http://localhost:9/cb", VERIFIER,
    )
    assert payload["access_token"]

    async def _verify():
        return await VaultVerifier(lambda: session).verify_token(payload["access_token"])

    verified = asyncio.run(_verify())
    assert verified is not None
    assert verified.claims.get("tenant_db_url") == url


def test_tool_serves_token_tenant(
    mcp_server, session: Session, tenant_world, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from mcp.server.auth.provider import AccessToken

    from pre.mcp_oauth import set_mcp_consent

    url_a = tenant_world["tenants"]["a@x.co"]["url"]
    url_b = tenant_world["tenants"]["b@x.co"]["url"]

    from pre.db import make_session_factory
    from pre.tenants import get_engine

    db_a = make_session_factory(get_engine(url_a))()
    try:
        set_mcp_consent(db_a, True, {"business"})
        fake = AccessToken(
            token="x", client_id="c", scopes=[], claims={"tenant_db_url": url_a}
        )
        monkeypatch.setattr("pre.mcp_server.get_access_token", lambda: fake)

        async def _go_a(server):
            return await server.call_tool("query_profile", {})

        text_a = json.dumps(asyncio.run(_go_a(mcp_server)).model_dump(), default=str)
        assert "Friend of a@x.co" in text_a
        assert "Friend of b@x.co" not in text_a
    finally:
        db_a.close()

    db_b = make_session_factory(get_engine(url_b))()
    try:
        set_mcp_consent(db_b, True, {"business"})
    finally:
        db_b.close()

    fake_b = AccessToken(
        token="y", client_id="c", scopes=[], claims={"tenant_db_url": url_b}
    )
    monkeypatch.setattr("pre.mcp_server.get_access_token", lambda: fake_b)

    async def _go_b(server):
        return await server.call_tool("query_profile", {})

    text_b = json.dumps(asyncio.run(_go_b(mcp_server)).model_dump(), default=str)
    assert "Friend of b@x.co" in text_b
    assert "Friend of a@x.co" not in text_b


def test_legacy_grant_routes_default(
    mcp_server, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio

    from pre.models import Person

    session.add(Person(display_name="Default DB friend"))
    session.flush()
    friend = session.query(Person).filter_by(display_name="Default DB friend").one()
    session.add(NetworkLink(person_id=friend.id))
    session.commit()
    monkeypatch.setattr("pre.mcp_server.get_access_token", lambda: None)

    async def _go():
        return await mcp_server.call_tool("query_profile", {})

    # Consent lives in the default DB here too (legacy single-DB semantics).
    from pre.mcp_oauth import set_mcp_consent

    set_mcp_consent(session, True, {"business"})
    text = json.dumps(asyncio.run(_go()).model_dump(), default=str)
    assert "Default DB friend" in text
