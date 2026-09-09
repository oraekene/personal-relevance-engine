"""MCP server outlet (issue 24): digest, verdicts, and consent-gated profile answers.

Mounted in the FastAPI app (one deployment). HTTP-layer auth comes from the
SDK bearer middleware backed by VaultVerifier; tools assume an authenticated
caller and enforce query consent themselves. Tool logic reuses the same
shaping as pages and JSON API (digest.item_json); only the transport is new.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import AnyHttpUrl
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pre.digest import item_json, list_digest_items
from pre.mcp_oauth import VaultVerifier, issuer_url, profile_query_scope
from pre.models import (
    Activity,
    Change,
    DigestItem,
    Goal,
    LifeDimension,
    Need,
    Task,
    provenance_of,
)
from pre.taxonomy import DIMENSIONS
from pre.verdicts import VALID_VERDICTS
from pre.verdicts import record_verdict as _record_verdict


def _prov(row: Any) -> dict[str, Any]:
    """Provenance as JSON-safe dict (timestamps rendered ISO)."""
    info = provenance_of(row)
    stamp = info.get("last_confirmed_at")
    info["last_confirmed_at"] = stamp.isoformat() if isinstance(stamp, datetime) else None
    return info


def profile_answer(
    session: Session, scope: set[str], dimension: str | None = None
) -> dict[str, Any]:
    """Answer-shaped dump of the consented Profile slice plus recent digest.

    Every assertion carries its provenance (user data); every Change carries a
    third-party tag — the answering model can weigh what it cites.
    """
    rows = {d.code: d for d in session.scalars(select(LifeDimension)).all()}
    out_dims = []
    for dim in DIMENSIONS:
        if dim.code not in scope or (dimension is not None and dim.code != dimension):
            continue
        row = rows.get(dim.code)
        goals = []
        if row is not None:
            for goal in session.scalars(select(Goal).where(Goal.dimension_id == row.id)).all():
                needs = []
                for need in session.scalars(select(Need).where(Need.goal_id == goal.id)).all():
                    activities = []
                    for activity in session.scalars(
                        select(Activity).where(Activity.need_id == need.id)
                    ).all():
                        tasks = []
                        for task in session.scalars(
                            select(Task).where(Task.activity_id == activity.id)
                        ).all():
                            tools = sorted(tt.tool.name for tt in task.task_tools)
                            tasks.append({"title": task.title, **_prov(task), "tools": tools})
                        activities.append(
                            {
                                "title": activity.title,
                                "cadence": activity.cadence,
                                **_prov(activity),
                                "tasks": tasks,
                            }
                        )
                    needs.append(
                        {
                            "title": need.title,
                            "horizon": need.horizon,
                            "pain_level": need.pain_level,
                            "openness_to_change": need.openness_to_change,
                            **_prov(need),
                            "activities": activities,
                        }
                    )
                goals.append({"title": goal.title, **_prov(goal), "needs": needs})
        out_dims.append(
            {
                "code": dim.code,
                "name": dim.name,
                "satisfaction": row.satisfaction_score if row else None,
                "goals": goals,
            }
        )
    network: list[dict[str, Any]] = []
    if dimension is None:
        from pre.models import NetworkLink

        for link in session.scalars(select(NetworkLink)).all():
            if link.person is not None:
                name, kind = link.person.display_name, "person"
            elif link.organization is not None:
                name, kind = link.organization.name, "organization"
            else:
                continue
            network.append(
                {
                    "kind": kind,
                    "name": name,
                    "role": link.role,
                    "frequency": link.frequency,
                    "recency": link.recency,
                    "dimension_code": link.dimension_code,
                    **_prov(link),
                }
            )
    recent = []
    for item in session.scalars(
        select(DigestItem).order_by(DigestItem.assembled_at.desc())
    ).all()[:10]:
        change = session.get(Change, item.change_id)
        recent.append(
            {
                "product": change.product_name if change else "?",
                "title": change.title if change else "?",
                "score": item.score,
                "reasoning": item.reasoning,
                "verdict": item.verdict,
                "provenance": {
                    "match": "user-profile",
                    "change": "third-party-change",
                    "reasoning": "model-generated",
                },
            }
        )
    return {"dimensions": out_dims, "network": network, "recent_digest": recent}


def create_mcp_server(session_factory: sessionmaker[Session]) -> MCPServer[Any]:
    """Build the MCP server. Mount it: app.mount("/mcp", server.streamable_http_app("/"))."""
    from mcp.server.auth.settings import AuthSettings

    base = issuer_url().rstrip("/")
    server: MCPServer[Any] = MCPServer(
        "personal-relevance-engine",
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(base),
            resource_server_url=AnyHttpUrl(f"{base}/mcp"),
            validate_token_resource=False,
        ),
        token_verifier=VaultVerifier(session_factory),
    )

    @server.tool(description="Read the assembled daily or weekly digest with scores and reasons.")
    def get_digest(kind: str) -> list[dict[str, Any]]:
        if kind not in ("daily", "weekly"):
            raise ValueError(f"unknown digest kind {kind!r}")
        session = session_factory()
        try:
            return [item_json(item) for item in list_digest_items(session, kind)]
        finally:
            session.close()

    @server.tool(description="Record an act/dismiss verdict on a digest item.")
    def record_verdict(item_id: int, choice: str) -> dict[str, Any]:
        if choice not in VALID_VERDICTS:
            raise ValueError("verdict must be act or dismiss")
        session = session_factory()
        try:
            log = _record_verdict(session, item_id, choice, channel="mcp")
            return {
                "item_id": item_id,
                "verdict": choice,
                "profile_version": log.profile_version,
            }
        finally:
            session.close()

    @server.tool(
        description="Answer from the user's profile: consented dimensions with "
        "provenance plus recent digest items. Refuses when assistant answers "
        "are disabled; pass a dimension code to narrow to one area."
    )
    def query_profile(dimension: str | None = None) -> dict[str, Any]:
        session = session_factory()
        try:
            scope = profile_query_scope(session)
            if scope is None:
                raise ValueError(
                    "assistant answers are disabled — enable them in Settings"
                )
            if dimension is not None and dimension not in scope:
                raise ValueError(f"unknown or not-allowed dimension {dimension!r}")
            return profile_answer(session, scope, dimension)
        finally:
            session.close()

    return server
