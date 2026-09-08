"""Web surface (ticket 06) grown into the first Outlet (ticket 23).

Human pages stay server-rendered; a JSON API beside them serves future Outlets
(assistant plugin, extension) and the pages themselves as they migrate. Pages
ride deployment auth (mesh/proxy); /api/* routes additionally require a bearer
token when PRE_API_TOKEN is set (unset keeps local-dev parity: open).

Run: `uvicorn pre.web:create_app --factory --host 0.0.0.0 --port 8787`
"""

from __future__ import annotations

import html
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pre.coldstart import get_mode
from pre.digest import ensure_matrix, mark_delivered
from pre.models import DigestItem
from pre.ops import render_ops_dashboard
from pre.verdicts import VALID_VERDICTS, record_verdict


def push_link(base_url: str, digest_item_id: int) -> str:
    """The URL a push channel (email/Telegram) sends the user to."""
    return f"{base_url.rstrip('/')}/item/{digest_item_id}/verdict/%s"


def _require_token(request: Request) -> None:
    """Bearer gate for /api/* routes (ticket 23: single-tenant token auth).

    One env-configured token implies the tenant. Unset keeps local-dev parity
    (deployment fronts auth); set requires `Authorization: Bearer <token>`.
    """
    expected = os.environ.get("PRE_API_TOKEN", "")
    if not expected:
        return
    if request.headers.get("authorization", "") != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="valid bearer token required")


class VerdictIn(BaseModel):
    item_id: int
    choice: str


def _item_json(item: DigestItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "change_id": item.change_id,
        "score": item.score,
        "entity_type": item.entity_type,
        "entity_id": item.entity_id,
        "entity_label": item.entity_label,
        "dimension_code": item.dimension_code,
        "reasoning": item.reasoning,
        "unscored": item.unscored,
        "stale": item.stale,
        "verdict": item.verdict,
        "delivered_at": item.delivered_at.isoformat() if item.delivered_at else None,
    }


def _page(title: str, body: str) -> str:
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{html.escape(title)}</title>"
        "<style>body{font-family:system-ui;margin:2rem auto;max-width:42rem;padding:0 1rem}"
        ".item{border:1px solid #ddd;border-radius:8px;padding:.75rem 1rem;margin:1rem 0}"
        ".score{font-weight:700}.flags{color:#b00;font-size:.85rem}"
        "a.btn{display:inline-block;padding:.3rem .9rem;margin-right:.4rem;"
        "border-radius:6px;text-decoration:none;border:1px solid #888}"
        ".act{background:#e7f5e7}.dismiss{background:#f5e7e7}</style></head>"
        f"<body><h1>{html.escape(title)}</h1>{body}"
        "</body></html>"
    )


def _item_html(session: Session, item: DigestItem) -> str:
    flags = []
    if item.unscored:
        flags.append("UNSCORED")
    if item.stale:
        flags.append("STALE PROFILE")
    flag_text = f"<span class='flags'>[{'/'.join(flags)}]</span> " if flags else ""
    dimension = f" ({item.dimension_code})" if item.dimension_code else ""
    verdict_text = ""
    if item.verdict:
        verdict_text = f" <b>{'✅ ACTED' if item.verdict == 'act' else '🗑 dismissed'}</b>"
    buttons = ""
    if item.verdict is None:
        act = f"/item/{item.id}/verdict/act"
        dismiss = f"/item/{item.id}/verdict/dismiss"
        buttons = (
            f"<a class='btn act' href='{act}'>Act</a>"
            f"<a class='btn dismiss' href='{dismiss}'>Dismiss</a>"
        )
    return (
        f"<div class='item'>"
        f"<span class='score'>{item.score}/100</span>{flag_text} "
        f"<b>{html.escape(item.entity_label)}</b>{dimension}{verdict_text}<br>"
        f"{html.escape(item.reasoning)}"
        f"<br>{buttons}"
        "</div>"
    )


def _digest_html(session: Session, kind: str) -> str:
    mark_delivered(session, kind)
    mode = get_mode(session)
    items = session.scalars(
        select(DigestItem).where(DigestItem.digest_kind == kind).order_by(DigestItem.score.desc())
    ).all()
    if not items:
        body = "<p>(nothing passed the thresholds)</p>"
    else:
        body = "".join(_item_html(session, item) for item in items)
    other = "weekly" if kind == "daily" else "daily"
    nav = f"<p><a href='/digest/{other}'>switch to {other}</a> · <a href='/'>overview</a></p>"
    shadow = "<p><i>[SHADOW MODE — nothing is delivered or marked read]</i></p>" if (
        mode == "shadow"
    ) else ""
    return _page(f"{kind} digest", shadow + body + nav)


def create_app(session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    """App factory. Pass a session_factory or rely on the default DB URL."""
    if session_factory is None:
        from pre.db import DEFAULT_DB_URL, init_db, make_engine, make_session_factory

        engine = make_engine(os.environ.get("PRE_DB_URL", DEFAULT_DB_URL))
        init_db(engine)
        session_factory = make_session_factory(engine)

    app = FastAPI(title="Personal Relevance Engine", docs_url=None, redoc_url=None)

    @app.get("/")
    def overview() -> Response:
        session = session_factory()
        try:
            daily = session.scalar(
                select(DigestItem).where(DigestItem.digest_kind == "daily")
            )
            weekly = session.scalar(
                select(DigestItem).where(DigestItem.digest_kind == "weekly")
            )
            body = (
                f"<p><a href='/digest/daily'>Daily digest</a> "
                f"({'has items' if daily else 'empty'})</p>"
                f"<p><a href='/digest/weekly'>Weekly digest</a> "
                f"({'has items' if weekly else 'empty'})</p>"
            )
            from pre.coldstart import coverage_gate

            gate = coverage_gate(session)
            state = get_mode(session)
            note = (
                f"<p>mode: {state} · gate: {'PASS' if gate.passed else 'not passed'}</p>"
            )
            return Response(_page("Personal Relevance Engine", body + note), media_type="text/html")
        finally:
            session.close()

    @app.get("/digest/{kind}")
    def digest(kind: str) -> Response:
        if kind not in ("daily", "weekly"):
            raise HTTPException(status_code=404, detail="unknown digest kind")
        session = session_factory()
        try:
            return Response(_digest_html(session, kind), media_type="text/html")
        finally:
            session.close()

    @app.get("/item/{item_id}/verdict/{choice}")
    def verdict(item_id: int, choice: str) -> Response:
        if choice not in VALID_VERDICTS:
            raise HTTPException(status_code=400, detail="verdict must be act or dismiss")
        session = session_factory()
        try:
            record_verdict(session, item_id, choice, channel="web")
            item = session.get(DigestItem, item_id)
            target = f"/digest/{item.digest_kind}" if item else "/"
            return RedirectResponse(target, status_code=303)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            session.close()

    @app.get("/api/digest/{kind}")
    def api_digest(kind: str, request: Request) -> dict[str, Any]:
        _require_token(request)
        if kind not in ("daily", "weekly"):
            raise HTTPException(status_code=404, detail="unknown digest kind")
        session = session_factory()
        try:
            items = session.scalars(
                select(DigestItem).where(DigestItem.digest_kind == kind).order_by(DigestItem.score.desc())
            ).all()
            return {
                "kind": kind,
                "mode": get_mode(session),
                "items": [_item_json(item) for item in items],
            }
        finally:
            session.close()

    @app.post("/api/verdict")
    def api_verdict(payload: VerdictIn, request: Request) -> dict[str, Any]:
        _require_token(request)
        if payload.choice not in VALID_VERDICTS:
            raise HTTPException(status_code=400, detail="verdict must be act or dismiss")
        session = session_factory()
        try:
            try:
                log_row = record_verdict(session, payload.item_id, payload.choice, channel="api")
            except ValueError as exc:
                message = str(exc)
                if "not found" in message:
                    raise HTTPException(status_code=404, detail=message) from exc
                raise HTTPException(status_code=409, detail=message) from exc
            return {
                "item_id": payload.item_id,
                "verdict": payload.choice,
                "profile_version": log_row.profile_version,
            }
        finally:
            session.close()

    @app.get("/api/matrix")
    def api_matrix(request: Request) -> dict[str, Any]:
        _require_token(request)
        session = session_factory()
        try:
            cells = ensure_matrix(session)
            return {
                "cells": [
                    {
                        "digest_kind": cell.digest_kind,
                        "dimension_code": cell.dimension_code,
                        "min_score": cell.min_score,
                        "tuning": cell.tuning,
                    }
                    for cell in sorted(
                        cells.values(), key=lambda c: (c.digest_kind, c.dimension_code)
                    )
                ]
            }
        finally:
            session.close()

    @app.get("/api/ops")
    def api_ops(request: Request) -> dict[str, str]:
        _require_token(request)
        session = session_factory()
        try:
            return {"dashboard": render_ops_dashboard(session)}
        finally:
            session.close()

    return app


__all__ = ["create_app", "push_link"]
