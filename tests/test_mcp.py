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
from pre.models import Change, DigestItem, Tool, VerdictLog
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
    assert "not-allowed" in str(exc_info.value.__cause__)


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
