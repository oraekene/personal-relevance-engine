"""Web surface (ticket 06) grown into the first Outlet (ticket 23).

Human pages stay server-rendered; a JSON API beside them serves future Outlets
(assistant plugin, extension) and the pages themselves as they migrate. Pages
ride deployment auth (mesh/proxy); /api/* routes additionally require a bearer
token when PRE_API_TOKEN is set (unset keeps local-dev parity: open).

Run: `uvicorn pre.web:create_app --factory --host 0.0.0.0 --port 8787`
"""

from __future__ import annotations

import os
import re
import tempfile
import urllib.parse
import uuid
from pathlib import Path
from typing import Any

import anyio
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pre.coldstart import coverage_gate, get_mode, go_live
from pre.cost_meter import month_to_date_cents
from pre.db import make_session_factory
from pre.digest import ensure_matrix, item_json, list_digest_items, mark_delivered, set_cell
from pre.extension import (
    get_consent,
    overlay_matches,
    record_capture,
    set_consent,
)
from pre.google import (
    authorization_url,
    check_state,
    exchange_code,
    fetch_account_email,
    is_configured,
    new_state,
    store_tokens,
)
from pre.ingest import IMPORTERS, import_file
from pre.intake import apply_interview_step
from pre.mcp_oauth import get_mcp_consent, set_mcp_consent
from pre.models import DigestItem, Goal, LifeDimension, Need, OAuthToken, SourceSyncState
from pre.ops import last_backup_at, render_ops_dashboard
from pre.push import (
    add_subscription,
    get_quiet_hours,
    list_subscriptions,
    remove_subscription,
    set_quiet_hours,
    vapid_public_key,
)
from pre.render import render_template
from pre.settings import PRESET_ORDER, apply_preset, preset_of
from pre.taxonomy import DIMENSIONS, DIMENSIONS_BY_CODE, checked_dimension_codes
from pre.tenants import (
    SESSION_COOKIE,
    SESSION_TTL_DAYS,
    LoginRequired,
    Tenant,
    create_session_token,
    destroy_session,
    effective_cap_cents,
    get_engine,
    get_registry,
    login_authorization_url,
    login_exchange,
    open_request_session,
    provision_sqlite_tenant,
    register_tenant,
)
from pre.verdicts import VALID_VERDICTS, record_verdict


def push_link(base_url: str, digest_item_id: int) -> str:
    """The URL a push channel (email/Telegram) sends the user to."""
    return f"{base_url.rstrip('/')}/item/{digest_item_id}/verdict/%s"


def _sources_error(session: Session, message: str, status: int) -> Response:
    return Response(
        render_template("sources.html", **_sources_view(session, error=message)),
        status_code=status,
        media_type="text/html",
    )


def _settings_error(session: Session, message: str) -> Response:
    return Response(
        render_template(
            "settings.html",
            rows=_settings_rows(session),
            presets=list(PRESET_ORDER),
            error=message,
        ),
        status_code=400,
        media_type="text/html",
    )


class UploadTooLarge(ValueError):
    """An uploaded export exceeds the configured cap."""


def _max_upload_bytes() -> int:
    raw = os.environ.get("PRE_MAX_UPLOAD_MB", "")
    try:
        mb = int(raw) if raw else 512
    except ValueError:
        mb = 512
    return max(1, mb) * 1024 * 1024


async def _save_upload(kind: str, upload: Any) -> Path:
    """Stream an upload to a temp file named after the original (sanitized)."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(getattr(upload, "filename", "") or "upload"))
    dest = Path(tempfile.gettempdir()) / f"pre-upload-{uuid.uuid4().hex}-{safe[-64:]}"
    cap = _max_upload_bytes()
    written = 0
    too_big = False
    async with await anyio.open_file(dest, "wb") as fh:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > cap:
                too_big = True
                break
            await fh.write(chunk)
    if too_big:
        dest.unlink(missing_ok=True)
        raise UploadTooLarge(f"file exceeds the {cap // (1024 * 1024)} MB upload cap")
    return dest


def _sources_view(session: Session, error: str | None = None) -> dict[str, Any]:
    states = session.scalars(select(SourceSyncState)).all()
    latest: dict[str, Any] = {}
    records: dict[str, int] = {}
    for st in states:
        prev = latest.get(st.tier)
        if prev is None or (st.last_sync_at is not None and st.last_sync_at > prev):
            latest[st.tier] = st.last_sync_at
        records[st.tier] = records.get(st.tier, 0) + (st.records_seen or 0)
    rows = []
    for kind, importer in IMPORTERS.items():
        synced = latest.get(importer.tier)
        rows.append(
            {
                "kind": kind,
                "tier": importer.tier,
                "last_sync": synced.date().isoformat() if synced is not None else None,
                "records": records.get(importer.tier, 0),
            }
        )
    accounts = [
        {
            "service": row.service,
            "email": row.account_email,
            "expires": row.expires_at.date().isoformat() if row.expires_at else None,
        }
        for row in session.scalars(select(OAuthToken)).all()
    ]
    return {
        "rows": rows,
        "error": error,
        "google_configured": is_configured(),
        "accounts": accounts,
    }


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


class CaptureIn(BaseModel):
    url: str
    title: str


class CaptureConsentIn(BaseModel):
    enabled: bool
    blocked_hosts: list[str] = []


class PushSubIn(BaseModel):
    endpoint: str
    keys: dict[str, str] = {}


class PushQuietIn(BaseModel):
    start_hour: int
    end_hour: int


SW_JS = """\
self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : {};
  event.waitUntil(
    self.registration.showNotification(data.title || "Relevance", {
      body: data.body || "",
      data: { url: data.url || "/digest/daily" },
    })
  );
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow(event.notification.data.url));
});
"""


class InterviewGoalIn(BaseModel):
    title: str
    needs: list[str] = []


class InterviewStepIn(BaseModel):
    satisfaction: int | None = None
    goals: list[InterviewGoalIn] = []


class SettingsIn(BaseModel):
    dimension_code: str
    preset: str


def _settings_rows(session: Session) -> list[dict[str, Any]]:
    cells = ensure_matrix(session)
    rows = []
    for dim in DIMENSIONS:
        daily = cells[("daily", dim.code)]
        weekly = cells[("weekly", dim.code)]
        tuning = "/".join(sorted({daily.tuning, weekly.tuning}))
        rows.append(
            {
                "code": dim.code,
                "name": dim.name,
                "daily": daily.min_score,
                "weekly": weekly.min_score,
                "preset": preset_of(daily.min_score, weekly.min_score),
                "tuning": tuning,
            }
        )
    return rows


def _interview_progress(session: Session) -> list[dict[str, Any]]:
    rows = {d.code: d for d in session.scalars(select(LifeDimension)).all()}
    steps = []
    for dim in DIMENSIONS:
        row = rows.get(dim.code)
        steps.append(
            {
                "code": dim.code,
                "name": dim.name,
                "description": dim.description,
                "sub_dimensions": list(dim.sub_dimensions),
                "satisfaction": row.satisfaction_score if row else None,
                "done": row is not None and row.satisfaction_score is not None,
            }
        )
    return steps


def _next_step_code(session: Session) -> str | None:
    for step in _interview_progress(session):
        if not step["done"]:
            return str(step["code"])
    return None


def _step_view(
    session: Session, code: str, error: str | None = None
) -> dict[str, Any] | None:
    steps = _interview_progress(session)
    position = next((i for i, s in enumerate(steps) if s["code"] == code), None)
    if position is None:
        return None
    goals: list[dict[str, Any]] = []
    row = session.scalar(select(LifeDimension).where(LifeDimension.code == code))
    if row is not None:
        for goal in session.scalars(select(Goal).where(Goal.dimension_id == row.id)).all():
            need_titles = [
                n.title for n in session.scalars(select(Need).where(Need.goal_id == goal.id)).all()
            ]
            goals.append({"title": goal.title, "needs": need_titles})
    return {
        "step": steps[position],
        "position": position + 1,
        "total": len(steps),
        "goals": goals,
        "error": error,
    }


def _digest_html(session: Session, kind: str) -> str:
    mark_delivered(session, kind)
    mode = get_mode(session)
    return render_template(
        "digest.html",
        kind=kind,
        mode=mode,
        items=[item_json(item) for item in list_digest_items(session, kind)],
        other="weekly" if kind == "daily" else "daily",
    )


def create_app(
    session_factory: sessionmaker[Session] | None = None,
    tenant_registry_url: str | None = None,
) -> FastAPI:
    """App factory. Pass a session_factory or rely on the default DB URL.

    tenant_registry_url turns on multi-tenant mode (None reads
    TENANT_REGISTRY_URL, empty means legacy single database).
    """
    if session_factory is None:
        from pre.db import DEFAULT_DB_URL, init_db, make_engine

        engine = make_engine(os.environ.get("PRE_DB_URL", DEFAULT_DB_URL))
        init_db(engine)
        session_factory = make_session_factory(engine)

    app = FastAPI(title="Personal Relevance Engine", docs_url=None, redoc_url=None)

    from pre.mcp_oauth import register_oauth_routes

    register_oauth_routes(app, session_factory, tenant_registry_url)

    from pre.mcp_server import create_mcp_server

    app.mount("/mcp", create_mcp_server(session_factory).streamable_http_app(streamable_http_path="/"))

    if tenant_registry_url is None:
        tenant_registry_url = os.environ.get("TENANT_REGISTRY_URL", "") or None

    def _open_tenant(request: Request) -> tuple[Session, Tenant | None]:
        return open_request_session(request.cookies, session_factory, tenant_registry_url)

    @app.exception_handler(LoginRequired)
    def _login_required(request: Request, exc: LoginRequired) -> Response:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "tenant login required"}, status_code=401)
        return RedirectResponse("/auth/google/login", status_code=303)

    @app.get("/")
    def overview(request: Request) -> Response:
        session, tenant = _open_tenant(request)
        try:
            daily = session.scalar(
                select(DigestItem).where(DigestItem.digest_kind == "daily")
            )
            weekly = session.scalar(
                select(DigestItem).where(DigestItem.digest_kind == "weekly")
            )
            gate = coverage_gate(session)
            state = get_mode(session)
            steps = _interview_progress(session)
            tiers = {s.tier for s in session.scalars(select(SourceSyncState)).all()}
            connected = sum(1 for _k, imp in IMPORTERS.items() if imp.tier in tiers)
            return Response(
                render_template(
                    "overview.html",
                    daily=bool(daily),
                    weekly=bool(weekly),
                    state=state,
                    gate_passed=gate.passed,
                    done_steps=sum(1 for s in steps if s["done"]),
                    total_steps=len(steps),
                    sources_connected=connected,
                    sources_total=len(IMPORTERS),
                    auth_email=tenant.email if tenant else None,
                ),
                media_type="text/html",
            )
        finally:
            session.close()

    @app.get("/digest/{kind}")
    def digest(kind: str, request: Request) -> Response:
        if kind not in ("daily", "weekly"):
            raise HTTPException(status_code=404, detail="unknown digest kind")
        session, _tenant = _open_tenant(request)
        try:
            return Response(_digest_html(session, kind), media_type="text/html")
        finally:
            session.close()

    @app.get("/item/{item_id}/verdict/{choice}")
    def verdict(item_id: int, choice: str, request: Request) -> Response:
        if choice not in VALID_VERDICTS:
            raise HTTPException(status_code=400, detail="verdict must be act or dismiss")
        session, _tenant = _open_tenant(request)
        try:
            record_verdict(session, item_id, choice, channel="web")
            item = session.get(DigestItem, item_id)
            target = f"/digest/{item.digest_kind}" if item else "/"
            return RedirectResponse(target, status_code=303)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        finally:
            session.close()

    @app.get("/interview")
    def interview_index(request: Request) -> Response:
        session, _tenant = _open_tenant(request)
        try:
            nxt = _next_step_code(session)
            target = f"/interview/{nxt}" if nxt else "/interview/done"
            return RedirectResponse(target, status_code=303)
        finally:
            session.close()

    @app.get("/interview/done")
    def interview_done(request: Request) -> Response:
        session, _tenant = _open_tenant(request)
        try:
            gate = coverage_gate(session)
            steps = _interview_progress(session)
            done = sum(1 for s in steps if s["done"])
            return Response(
                render_template(
                    "interview_done.html",
                    passed=gate.passed,
                    failures=gate.failures,
                    done=done,
                    total=len(steps),
                    error=None,
                ),
                media_type="text/html",
            )
        finally:
            session.close()

    @app.post("/interview/go-live")
    def interview_go_live(request: Request) -> Response:
        session, _tenant = _open_tenant(request)
        try:
            try:
                go_live(session)
            except PermissionError:
                gate = coverage_gate(session)
                steps = _interview_progress(session)
                done = sum(1 for s in steps if s["done"])
                return Response(
                    render_template(
                        "interview_done.html",
                        passed=False,
                        failures=gate.failures,
                        done=done,
                        total=len(steps),
                        error="Coverage gate has not passed yet.",
                    ),
                    status_code=409,
                    media_type="text/html",
                )
            return RedirectResponse("/", status_code=303)
        finally:
            session.close()

    @app.get("/interview/{code}")
    def interview_step(code: str, request: Request) -> Response:
        session, _tenant = _open_tenant(request)
        try:
            view = _step_view(session, code)
            if view is None:
                raise HTTPException(status_code=404, detail="unknown dimension")
            return Response(render_template("interview_step.html", **view), media_type="text/html")
        finally:
            session.close()

    @app.post("/interview/{code}")
    async def interview_submit(code: str, request: Request) -> Response:
        if code not in DIMENSIONS_BY_CODE:
            raise HTTPException(status_code=404, detail="unknown dimension")
        form = await request.form()
        raw_satisfaction = form.get("satisfaction")
        satisfaction = raw_satisfaction if isinstance(raw_satisfaction, str) else None
        goals = []
        for i in range(5):
            title = str(form.get(f"goal_{i}", "") or "").strip()
            if not title:
                continue
            raw = str(form.get(f"goal_{i}_needs", "") or "")
            goals.append(
                {
                    "title": title,
                    "needs": [ln.strip() for ln in raw.splitlines() if ln.strip()],
                }
            )
        session, _tenant = _open_tenant(request)
        try:
            try:
                apply_interview_step(session, code, satisfaction, goals)
            except (ValueError, TypeError) as exc:
                view = _step_view(session, code, error=str(exc))
                assert view is not None  # code checked above
                return Response(
                    render_template("interview_step.html", **view),
                    status_code=400,
                    media_type="text/html",
                )
            return RedirectResponse("/interview", status_code=303)
        finally:
            session.close()

    @app.get("/sources")
    def sources(request: Request) -> Response:
        session, _tenant = _open_tenant(request)
        try:
            return Response(
                render_template("sources.html", **_sources_view(session)), media_type="text/html"
            )
        finally:
            session.close()

    @app.post("/sources/{kind}")
    async def sources_upload(kind: str, request: Request) -> Response:
        if kind not in IMPORTERS:
            raise HTTPException(status_code=404, detail="unknown source kind")
        form = await request.form()
        upload = form.get("file")
        session, _tenant = _open_tenant(request)
        dest: Path | None = None
        try:
            if upload is None or not hasattr(upload, "read"):
                return _sources_error(session, "Choose a file to import.", 400)
            try:
                dest = await _save_upload(kind, upload)
            except (ValueError, OSError) as exc:
                code = 413 if isinstance(exc, UploadTooLarge) else 400
                return _sources_error(session, str(exc), code)
            try:
                import_file(session, kind, dest)
            except (ValueError, KeyError, OSError) as exc:
                return _sources_error(session, f"Could not import that file: {exc}", 400)
            return RedirectResponse("/sources", status_code=303)
        finally:
            if dest is not None:
                dest.unlink(missing_ok=True)
            session.close()

    @app.post("/api/sources/{kind}")
    async def api_sources_upload(kind: str, request: Request) -> dict[str, Any]:
        _require_token(request)
        if kind not in IMPORTERS:
            raise HTTPException(status_code=404, detail="unknown source kind")
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=400, detail="multipart field 'file' is required")
        try:
            dest = await _save_upload(kind, upload)
        except UploadTooLarge as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except OSError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        session, _tenant = _open_tenant(request)
        try:
            try:
                result = import_file(session, kind, dest)
            except (ValueError, KeyError, OSError) as exc:
                raise HTTPException(status_code=400, detail=f"Could not import: {exc}") from exc
            return {
                "kind": kind,
                "tier": result.tier,
                "first_connect": result.first_connect,
                "proposals_new": result.proposals_new,
                "proposals_strengthened": result.proposals_strengthened,
                "skipped_known": result.skipped_known,
                "auto_accepted": result.auto_accepted,
            }
        finally:
            dest.unlink(missing_ok=True)
            session.close()

    @app.get("/settings")
    def settings_page(request: Request) -> Response:
        session, _tenant = _open_tenant(request)
        try:
            master, dims = get_mcp_consent(session)
            return Response(
                render_template(
                    "settings.html",
                    rows=_settings_rows(session),
                    presets=list(PRESET_ORDER),
                    error=None,
                    consent_master=master,
                    consent_dims=dims if dims is not None else {d.code for d in DIMENSIONS},
                    dimensions=DIMENSIONS,
                ),
                media_type="text/html",
            )
        finally:
            session.close()

    @app.post("/settings")
    async def settings_save(request: Request) -> Response:
        form = await request.form()
        session, _tenant = _open_tenant(request)
        try:
            try:
                for dim in DIMENSIONS:
                    wanted = str(form.get(f"preset_{dim.code}", "") or "")
                    if wanted:
                        # A chosen preset wins over the number fields for its row.
                        apply_preset(session, dim.code, wanted)
                        continue
                    for kind in ("daily", "weekly"):
                        raw = form.get(f"cell_{kind}_{dim.code}", "")
                        if raw in (None, ""):
                            continue
                        set_cell(session, kind, dim.code, int(str(raw)))
            except ValueError as exc:
                return _settings_error(session, str(exc))
            return RedirectResponse("/settings", status_code=303)
        finally:
            session.close()

    @app.post("/settings/consent")
    async def settings_consent_save(request: Request) -> Response:
        form = await request.form()
        master = bool(form.get("mcp_master"))
        checked = checked_dimension_codes(form)
        session, _tenant = _open_tenant(request)
        try:
            set_mcp_consent(session, master, checked)
            return RedirectResponse("/settings", status_code=303)
        finally:
            session.close()

    @app.get("/api/settings")
    def api_settings(request: Request) -> dict[str, Any]:
        _require_token(request)
        session, _tenant = _open_tenant(request)
        try:
            master, dims = get_mcp_consent(session)
            return {
                "rows": _settings_rows(session),
                "mcp_consent": {
                    "master": master,
                    "dimensions": sorted(dims) if dims is not None else None,
                },
            }
        finally:
            session.close()

    @app.post("/api/settings")
    def api_settings_save(payload: SettingsIn, request: Request) -> dict[str, Any]:
        _require_token(request)
        if payload.dimension_code not in DIMENSIONS_BY_CODE:
            raise HTTPException(status_code=404, detail="unknown dimension")
        session, _tenant = _open_tenant(request)
        try:
            try:
                daily, weekly = apply_preset(session, payload.dimension_code, payload.preset)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {
                "dimension_code": payload.dimension_code,
                "preset": payload.preset,
                "daily": daily,
                "weekly": weekly,
            }
        finally:
            session.close()

    @app.get("/auth/google/login")
    def auth_login() -> Response:
        if not os.environ.get("PRE_GOOGLE_CLIENT_ID"):
            return Response("Google login is not configured on this server.", status_code=400)
        if tenant_registry_url is None:
            return RedirectResponse("/", status_code=303)
        session = session_factory()
        try:
            state = new_state(session, "google_login_state")
        finally:
            session.close()
        return RedirectResponse(login_authorization_url(state), status_code=303)

    @app.get("/auth/google/callback")
    def auth_callback(request: Request) -> Response:
        params = request.query_params
        if params.get("error"):
            return Response("Google sign-in was denied.", status_code=400)
        session = session_factory()
        try:
            if not check_state(session, params.get("state"), "google_login_state"):
                return Response("Invalid or expired login — start over.", status_code=400)
            try:
                email = login_exchange(params.get("code") or "")
            except (ValueError, OSError, RuntimeError) as exc:
                return Response(f"Google sign-in failed: {exc}", status_code=400)
            if tenant_registry_url is None:
                return Response("Single-user server — no login needed.", status_code=400)
            tenant_base = os.environ.get("TENANT_DBS_DIR", "")
            if not tenant_base:
                parsed = urllib.parse.urlparse(tenant_registry_url)
                tenant_base = str(Path(parsed.path).parent / "tenants") if parsed.scheme == "sqlite" else "./tenants"
            db_url = provision_sqlite_tenant(tenant_base, email)
            reg_engine = get_registry(tenant_registry_url)
            reg_factory = make_session_factory(reg_engine)
            reg = reg_factory()
            try:
                tenant = register_tenant(reg, email, db_url)
                token = create_session_token(reg, tenant.id)
            finally:
                reg.close()
            response = RedirectResponse("/", status_code=303)
            response.set_cookie(
                SESSION_COOKIE, token, httponly=True, samesite="lax",
                max_age=SESSION_TTL_DAYS * 86400, path="/",
            )
            return response
        finally:
            session.close()

    @app.post("/auth/logout")
    def auth_logout(request: Request) -> Response:
        if tenant_registry_url is not None:
            token = request.cookies.get(SESSION_COOKIE, "")
            if token:
                reg = make_session_factory(get_registry(tenant_registry_url))()
                try:
                    destroy_session(reg, token)
                finally:
                    reg.close()
        response = RedirectResponse("/", status_code=303)
        response.delete_cookie(SESSION_COOKIE, path="/")
        return response

    @app.get("/sources/connect/google")
    def sources_connect_google(request: Request) -> Response:
        session, _tenant = _open_tenant(request)
        try:
            if not is_configured():
                return Response(
                    render_template(
                        "sources.html",
                        **_sources_view(
                            session,
                            error="Google OAuth is not configured "
                            "(set PRE_GOOGLE_CLIENT_ID and PRE_GOOGLE_CLIENT_SECRET).",
                        ),
                    ),
                    status_code=400,
                    media_type="text/html",
                )
            return RedirectResponse(authorization_url(new_state(session)), status_code=303)
        finally:
            session.close()

    @app.get("/sources/oauth/google/callback")
    def sources_oauth_callback(request: Request) -> Response:
        params = request.query_params

        if params.get("error"):
            session, _tenant = _open_tenant(request)
            try:
                return _sources_error(session, "Google authorization was denied.", 400)
            finally:
                session.close()
        session, _tenant = _open_tenant(request)
        try:
            code = params.get("code")
            if not code or not check_state(session, params.get("state")):
                return _sources_error(session, "Invalid OAuth state — start over from Sources.", 400)
            try:
                payload = exchange_code(code)
                email = fetch_account_email(str(payload["access_token"]))
                store_tokens(session, payload, email)
            except (ValueError, KeyError, OSError, RuntimeError) as exc:
                return _sources_error(session, f"Google connect failed: {exc}", 400)
            return RedirectResponse("/sources", status_code=303)
        finally:
            session.close()

    @app.post("/sources/disconnect/google")
    def sources_disconnect_google(request: Request) -> Response:
        session, _tenant = _open_tenant(request)
        try:
            row = session.scalar(select(OAuthToken).where(OAuthToken.service == "google"))
            if row is not None:
                session.delete(row)
                session.commit()
            return RedirectResponse("/sources", status_code=303)
        finally:
            session.close()

    @app.get("/api/digest/{kind}")
    def api_digest(kind: str, request: Request) -> dict[str, Any]:
        _require_token(request)
        if kind not in ("daily", "weekly"):
            raise HTTPException(status_code=404, detail="unknown digest kind")
        session, _tenant = _open_tenant(request)
        try:
            return {
                "kind": kind,
                "mode": get_mode(session),
                "items": [item_json(item) for item in list_digest_items(session, kind)],
            }
        finally:
            session.close()

    @app.post("/api/verdict")
    def api_verdict(payload: VerdictIn, request: Request) -> dict[str, Any]:
        _require_token(request)
        if payload.choice not in VALID_VERDICTS:
            raise HTTPException(status_code=400, detail="verdict must be act or dismiss")
        session, _tenant = _open_tenant(request)
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
        session, _tenant = _open_tenant(request)
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

    @app.get("/api/ops/tenants")
    def api_ops_tenants(request: Request) -> dict[str, Any]:
        """Operator rollup: spend, cap, and backup state per tenant.

        Gated by a dedicated operator token (PRE_OPERATOR_TOKEN), never the
        tenant bearer: tenants must not enumerate each other.
        """
        expected = os.environ.get("PRE_OPERATOR_TOKEN", "")
        if not expected or request.headers.get("authorization", "") != f"Bearer {expected}":
            raise HTTPException(status_code=403, detail="operator access required")
        if tenant_registry_url is None:
            raise HTTPException(status_code=400, detail="multi-tenant mode only")
        reg = make_session_factory(get_registry(tenant_registry_url))()
        try:
            rows = []
            for tenant in reg.scalars(select(Tenant)).all():
                tenant_session = make_session_factory(get_engine(tenant.db_url))()
                try:
                    spent = month_to_date_cents(tenant_session)
                    backup = last_backup_at(tenant_session)
                finally:
                    tenant_session.close()
                rows.append(
                    {
                        "email": tenant.email,
                        "spent_cents": spent,
                        "cap_cents": effective_cap_cents(tenant),
                        "last_backup": backup.isoformat() if backup else None,
                    }
                )
            return {"tenants": rows}
        finally:
            reg.close()

    @app.get("/api/interview")
    def api_interview(request: Request) -> dict[str, Any]:
        _require_token(request)
        session, _tenant = _open_tenant(request)
        try:
            steps = _interview_progress(session)
            gate = coverage_gate(session)
            return {
                "steps": [
                    {
                        "code": s["code"],
                        "name": s["name"],
                        "done": s["done"],
                        "satisfaction": s["satisfaction"],
                    }
                    for s in steps
                ],
                "done": sum(1 for s in steps if s["done"]),
                "total": len(steps),
                "gate_passed": gate.passed,
                "gate_failures": gate.failures,
            }
        finally:
            session.close()

    @app.post("/api/interview/{code}")
    def api_interview_step(
        code: str, payload: InterviewStepIn, request: Request
    ) -> dict[str, Any]:
        _require_token(request)
        if code not in DIMENSIONS_BY_CODE:
            raise HTTPException(status_code=404, detail="unknown dimension")
        session, _tenant = _open_tenant(request)
        try:
            try:
                apply_interview_step(
                    session,
                    code,
                    payload.satisfaction,
                    [g.model_dump() for g in payload.goals],
                )
            except (ValueError, TypeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"code": code, "next": _next_step_code(session)}
        finally:
            session.close()

    @app.post("/api/go-live")
    def api_go_live(request: Request) -> dict[str, Any]:
        _require_token(request)
        session, _tenant = _open_tenant(request)
        try:
            try:
                go_live(session)
            except PermissionError:
                gate = coverage_gate(session)
                raise HTTPException(status_code=409, detail={"failures": gate.failures}) from None
            return {"mode": "live"}
        finally:
            session.close()

    @app.post("/api/capture")
    def api_capture(payload: CaptureIn, request: Request) -> dict[str, Any]:
        """Browser extension lane: capture the visited page into the corpus."""
        _require_token(request)
        session, _tenant = _open_tenant(request)
        try:
            try:
                outcome = record_capture(session, payload.url, payload.title)
            except PermissionError as exc:
                raise HTTPException(status_code=403, detail=str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"outcome": outcome.outcome, "product": outcome.product}
        finally:
            session.close()

    @app.get("/api/capture/status")
    def api_capture_status(request: Request) -> dict[str, Any]:
        _require_token(request)
        session, _tenant = _open_tenant(request)
        try:
            enabled, blocked = get_consent(session)
            return {"enabled": enabled, "blocked_hosts": sorted(blocked)}
        finally:
            session.close()

    @app.post("/api/capture/consent")
    def api_capture_consent(payload: CaptureConsentIn, request: Request) -> dict[str, Any]:
        _require_token(request)
        session, _tenant = _open_tenant(request)
        try:
            set_consent(session, payload.enabled, set(payload.blocked_hosts))
            enabled, blocked = get_consent(session)
            return {"enabled": enabled, "blocked_hosts": sorted(blocked)}
        finally:
            session.close()

    @app.get("/api/overlay")
    def api_overlay(url: str, request: Request) -> dict[str, Any]:
        """Page overlay: digest items matching the visited page's host."""
        _require_token(request)
        session, _tenant = _open_tenant(request)
        try:
            return {"matches": overlay_matches(session, url)}
        finally:
            session.close()

    @app.get("/manifest.webmanifest")
    def pwa_manifest() -> dict[str, Any]:
        """Installable-web-app manifest (the TWA wraps this)."""
        return {
            "name": "Personal Relevance Engine",
            "short_name": "Relevance",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#111111",
            "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml"}],
        }

    @app.get("/sw.js")
    def service_worker() -> Response:
        """Push receiver: shows the notification, opens the digest on tap."""
        return Response(SW_JS, media_type="application/javascript")

    @app.get("/icon.svg")
    def app_icon() -> Response:
        from fastapi.responses import FileResponse

        return FileResponse(Path(__file__).with_name("icon.svg"), media_type="image/svg+xml")

    @app.get("/.well-known/assetlinks.json")
    def assetlinks() -> list[dict[str, Any]]:
        """Digital Asset Links: binds the domain to the Play app (TWA)."""
        package = os.environ.get("PRE_ANDROID_PACKAGE", "")
        sha256 = os.environ.get("PRE_ASSETLINKS_SHA256", "")
        if not package or not sha256:
            return []
        return [
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": package,
                    "sha256_cert_fingerprints": [sha256],
                },
            }
        ]

    @app.get("/api/push/vapid-public-key")
    def api_vapid_key() -> dict[str, str]:
        return {"public_key": vapid_public_key()}

    @app.post("/api/push/subscribe")
    def api_push_subscribe(payload: PushSubIn, request: Request) -> dict[str, Any]:
        _require_token(request)
        session, _tenant = _open_tenant(request)
        try:
            try:
                row = add_subscription(session, payload.endpoint, payload.keys)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            return {"endpoint": row.endpoint}
        finally:
            session.close()

    @app.post("/api/push/unsubscribe")
    def api_push_unsubscribe(payload: PushSubIn, request: Request) -> dict[str, Any]:
        _require_token(request)
        session, _tenant = _open_tenant(request)
        try:
            remove_subscription(session, payload.endpoint)
            return {"endpoint": payload.endpoint}
        finally:
            session.close()

    @app.get("/api/push/status")
    def api_push_status(request: Request) -> dict[str, Any]:
        _require_token(request)
        session, _tenant = _open_tenant(request)
        try:
            return {"subscriptions": [s.endpoint for s in list_subscriptions(session)]}
        finally:
            session.close()

    @app.get("/api/push/quiet")
    def api_push_quiet_get(request: Request) -> dict[str, int]:
        _require_token(request)
        session, _tenant = _open_tenant(request)
        try:
            start, end = get_quiet_hours(session)
            return {"start_hour": start, "end_hour": end}
        finally:
            session.close()

    @app.post("/api/push/quiet")
    def api_push_quiet_set(payload: PushQuietIn, request: Request) -> dict[str, int]:
        _require_token(request)
        session, _tenant = _open_tenant(request)
        try:
            try:
                set_quiet_hours(session, payload.start_hour, payload.end_hour)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            start, end = get_quiet_hours(session)
            return {"start_hour": start, "end_hour": end}
        finally:
            session.close()

    return app


__all__ = ["create_app", "push_link"]
