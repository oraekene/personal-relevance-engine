"""Self-issued OAuth for the MCP server (issue 24).

Minimal compliant issuer for one user: dynamic client registration (RFC 7591),
authorization-code flow with mandatory PKCE S256, refresh rotation, and RFC
8414/8707 metadata. Tokens and codes are random; only sha256 hashes rest in
the DB — nothing to recover, nothing to rotate cryptographically.

Human approval on the authorize screen is gated by the API token (proof of
operator access); minting refuses to run with no API token configured.
Granting the profile:read scope records query consent (master switch plus
per-dimension allowlist — the same flags the settings UI edits).
"""

from __future__ import annotations

import hashlib
import os
import secrets
import urllib.parse
from base64 import urlsafe_b64encode
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from mcp.server.auth.provider import AccessToken
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

# Shared SystemFlag nonce pattern lives in pre.google (its OAuth flow came
# first); the MCP issuer reuses it under its own key.
from pre.google import check_state, new_state
from pre.models import MCPAuthCode, MCPOAuthClient, MCPToken, SystemFlag
from pre.taxonomy import DIMENSIONS, checked_dimension_codes

SCOPES = ("digest:read", "verdict:write", "profile:read")
SCOPE_DESCRIPTIONS = {
    "digest:read": "Read your daily and weekly digests",
    "verdict:write": "Record act/dismiss verdicts you dictate",
    "profile:read": "Answer questions from your profile",
}
CODE_TTL_SECONDS = 600
ACCESS_TTL_SECONDS = 3600
REFRESH_TTL_SECONDS = 30 * 86400
MCP_CONSENT_MASTER = "mcp_consent_master"
MCP_CONSENT_DIMENSIONS = "mcp_consent_dimensions"
MCP_CSRF_KEY = "mcp_oauth_csrf"


class _InvalidClient(ValueError):
    """Unknown client or bad secret: maps to invalid_client (401)."""


class _InvalidGrant(ValueError):
    """Bad code/verifier/redirect/refresh: maps to invalid_grant (400)."""


def issuer_url() -> str:
    return os.environ.get("PRE_PUBLIC_URL", "http://127.0.0.1:8787")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


def _now() -> datetime:
    return datetime.now(UTC)


def _valid_redirect(uri: str) -> bool:
    try:
        parts = urllib.parse.urlparse(uri)
    except ValueError:
        return False
    if parts.scheme == "https" and parts.hostname:
        return True
    return parts.scheme == "http" and parts.hostname in (
        "localhost",
        "127.0.0.1",
        "[::1]",
        "::1",
    )


def get_mcp_consent(session: Session) -> tuple[bool, set[str] | None]:
    """(master switch, allowed dimensions or None for all). Default: off."""
    master = session.scalar(select(SystemFlag).where(SystemFlag.key == MCP_CONSENT_MASTER))
    if master is None or master.value != "1":
        return False, set()
    dims = session.scalar(select(SystemFlag).where(SystemFlag.key == MCP_CONSENT_DIMENSIONS))
    if dims is None:
        return True, None
    return True, {c.strip() for c in dims.value.split(",") if c.strip()}


def set_mcp_consent(session: Session, master: bool, dimensions: set[str]) -> None:
    """Persist query consent. An empty set allows nothing (master on, zero areas)."""
    for key, value in (
        (MCP_CONSENT_MASTER, "1" if master else "0"),
        (MCP_CONSENT_DIMENSIONS, ",".join(sorted(dimensions))),
    ):
        flag = session.scalar(select(SystemFlag).where(SystemFlag.key == key))
        if flag is None:
            session.add(SystemFlag(key=key, value=value))
        else:
            flag.value = value
    session.commit()


def profile_query_scope(session: Session) -> set[str] | None:
    """Life Dimension codes the assistant may answer from; None refuses (master off).

    Unknown codes are filtered so stale flags can never widen access.
    """
    master, dims = get_mcp_consent(session)
    if not master:
        return None
    allowed = {d.code for d in DIMENSIONS}
    return allowed if dims is None else (set(dims) & allowed)


def register_client(
    session: Session,
    redirect_uris: list[str],
    client_name: str = "",
    scope: str = "",
) -> dict[str, Any]:
    """Dynamic client registration (RFC 7591). Returns the record incl. one-time secret."""
    if not redirect_uris or not all(isinstance(u, str) and _valid_redirect(u) for u in redirect_uris):
        raise ValueError("redirect_uris must be non-empty https (or localhost http) URLs")
    wanted = scope.split() if scope else list(SCOPES)
    if not wanted or any(s not in SCOPES for s in wanted):
        raise ValueError(f"scope must be a subset of {list(SCOPES)}")
    client_id = secrets.token_hex(16)
    secret = secrets.token_urlsafe(32)
    session.add(
        MCPOAuthClient(
            client_id=client_id,
            client_secret_hash=_digest(secret),
            redirect_uris_json=list(redirect_uris),
            client_name=client_name,
            scopes_json=wanted,
        )
    )
    session.commit()
    return {
        "client_id": client_id,
        "client_secret": secret,
        "redirect_uris": list(redirect_uris),
        "client_name": client_name,
        "scope": " ".join(wanted),
    }


def _get_client(session: Session, client_id: str) -> MCPOAuthClient:
    row = session.scalar(select(MCPOAuthClient).where(MCPOAuthClient.client_id == client_id))
    if row is None:
        raise _InvalidClient("unknown client")
    return row


def _check_client_secret(row: MCPOAuthClient, secret: str | None) -> None:
    if row.client_secret_hash is None:
        return  # public client: PKCE alone authenticates
    if not secret or not secrets.compare_digest(row.client_secret_hash, _digest(secret)):
        raise _InvalidClient("bad client secret")


def split_scopes(scope: str | None, allowed: list[str]) -> list[str]:
    wanted = (scope or "").split()
    if not wanted or any(s not in allowed for s in wanted):
        raise ValueError(f"scope must be a non-empty subset of {allowed}")
    return wanted


def validate_authorize(
    session: Session,
    client_id: str | None,
    redirect_uri: str | None,
    scope: str | None,
    code_challenge: str | None,
    code_challenge_method: str | None,
) -> tuple[MCPOAuthClient, list[str]]:
    """Pre-redirect validation. Failure here must NOT redirect (untrusted target)."""
    if not client_id:
        raise ValueError("client_id is required")
    row = session.scalar(select(MCPOAuthClient).where(MCPOAuthClient.client_id == client_id))
    if row is None:
        raise ValueError("unknown client")
    if not redirect_uri or redirect_uri not in (row.redirect_uris_json or []):
        raise ValueError("redirect_uri must exactly match a registered one")
    scopes = split_scopes(scope, row.scopes_json or list(SCOPES))
    if not code_challenge:
        raise ValueError("code_challenge (PKCE S256) is required")
    if (code_challenge_method or "S256") != "S256":
        raise ValueError("only PKCE S256 is supported")
    return row, scopes


def approve_authorize(
    session: Session,
    client_id: str,
    tenant_db_url: str,
    redirect_uri: str,
    scopes: list[str],
    code_challenge: str,
    api_token: str | None,
    allow_profile: bool,
    dimensions: set[str],
) -> str:
    """Mint a single-use code after operator approval. Returns the plaintext code."""
    expected = os.environ.get("PRE_API_TOKEN", "")
    if not expected:
        raise RuntimeError("server has no API token configured — cannot mint assistant credentials")
    if not api_token or not secrets.compare_digest(api_token, expected):
        raise ValueError("wrong API token")
    if "profile:read" in scopes:
        set_mcp_consent(session, allow_profile, dimensions)
    code = secrets.token_urlsafe(32)
    session.add(
        MCPAuthCode(
            code_hash=_digest(code),
            client_id=client_id,
            tenant_db_url=tenant_db_url,
            redirect_uri=redirect_uri,
            scopes_json=scopes,
            code_challenge=code_challenge,
            used=False,
            expires_at=_now() + timedelta(seconds=CODE_TTL_SECONDS),
        )
    )
    session.commit()
    return code


def _pkce_ok(verifier: str, challenge: str) -> bool:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(computed, challenge)


def _issue_pair(
    session: Session, client_id: str, tenant_db_url: str, scopes: list[str]
) -> tuple[str, str]:
    access = secrets.token_urlsafe(32)
    refresh = secrets.token_urlsafe(32)
    now = _now()
    row = session.scalar(
        select(MCPToken).where(
            MCPToken.client_id == client_id, MCPToken.tenant_db_url == tenant_db_url
        )
    )
    values = {
        "access_hash": _digest(access),
        "refresh_hash": _digest(refresh),
        "scopes_json": scopes,
        "access_expires_at": now + timedelta(seconds=ACCESS_TTL_SECONDS),
        "refresh_expires_at": now + timedelta(seconds=REFRESH_TTL_SECONDS),
    }
    if row is None:
        session.add(MCPToken(client_id=client_id, tenant_db_url=tenant_db_url, **values))
    else:
        for key, value in values.items():
            setattr(row, key, value)
    session.commit()
    return access, refresh


def exchange_authorization_code(
    session: Session,
    client_id: str,
    client_secret: str | None,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, Any]:
    row = _get_client(session, client_id)
    _check_client_secret(row, client_secret)
    stored = session.scalar(select(MCPAuthCode).where(MCPAuthCode.code_hash == _digest(code)))
    if (
        stored is None
        or stored.used
        or _naive(_now()) > _naive(stored.expires_at)
        or stored.client_id != client_id
        or stored.redirect_uri != redirect_uri
        or not _pkce_ok(code_verifier, stored.code_challenge)
    ):
        raise _InvalidGrant("bad code, redirect, or verifier")
    stored.used = True
    session.commit()
    access, refresh = _issue_pair(session, client_id, stored.tenant_db_url, stored.scopes_json or [])
    scopes = stored.scopes_json or []
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL_SECONDS,
        "refresh_token": refresh,
        "scope": " ".join(scopes),
    }


def refresh_access_token(
    session: Session, client_id: str, client_secret: str | None, refresh_token: str
) -> dict[str, Any]:
    row = _get_client(session, client_id)
    _check_client_secret(row, client_secret)
    stored = session.scalar(
        select(MCPToken).where(MCPToken.refresh_hash == _digest(refresh_token))
    )
    if (
        stored is None
        or stored.client_id != client_id
        or _naive(_now()) > _naive(stored.refresh_expires_at)
    ):
        raise _InvalidGrant("bad or expired refresh token")
    access, refresh = _issue_pair(session, client_id, stored.tenant_db_url, stored.scopes_json or [])
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": ACCESS_TTL_SECONDS,
        "refresh_token": refresh,
        "scope": " ".join(stored.scopes_json or []),
    }


def metadata_authorization_server() -> dict[str, Any]:
    base = issuer_url().rstrip("/")
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "scopes_supported": list(SCOPES),
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["client_secret_post"],
    }


def metadata_protected_resource() -> dict[str, Any]:
    base = issuer_url().rstrip("/")
    return {
        "resource": f"{base}/mcp",
        "authorization_servers": [base],
        "scopes_supported": list(SCOPES),
    }


class VaultVerifier:
    """TokenVerifier: opaque access tokens checked against the hashed vault.

    The match location becomes the token's home: legacy rows (empty URL) route
    to the default database, tenant rows to theirs. Single lookup, no scan —
    all issuer rows live in the default database by design.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._factory = session_factory

    async def verify_token(self, token: str) -> AccessToken | None:
        session = self._factory()
        try:
            row = session.scalar(
                select(MCPToken).where(MCPToken.access_hash == _digest(token))
            )
            if row is None or _naive(_now()) > _naive(row.access_expires_at):
                return None
            return AccessToken(
                token=token,
                client_id=row.client_id,
                scopes=list(row.scopes_json or []),
                claims={"tenant_db_url": row.tenant_db_url or None},
            )
        finally:
            session.close()


def register_oauth_routes(
    app: FastAPI,
    session_factory: sessionmaker[Session],
    tenant_registry_url: str | None = None,
) -> None:
    """Mount the issuer endpoints on the FastAPI app (called from create_app)."""
    from pre.render import render_template

    def _session() -> Session:
        return session_factory()

    @app.get("/.well-known/oauth-authorization-server")
    def oauth_metadata() -> dict[str, Any]:
        return metadata_authorization_server()

    @app.get("/.well-known/oauth-protected-resource")
    def protected_metadata() -> dict[str, Any]:
        return metadata_protected_resource()

    @app.post("/oauth/register")
    async def oauth_register(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        session = _session()
        try:
            try:
                record = register_client(
                    session,
                    list(body.get("redirect_uris") or []),
                    str(body.get("client_name", "") or ""),
                    str(body.get("scope", "") or ""),
                )
            except (ValueError, TypeError, AttributeError) as exc:
                return JSONResponse({"error": "invalid_client_metadata", "detail": str(exc)}, status_code=400)
            return JSONResponse(record, status_code=201)
        finally:
            session.close()

    @app.get("/oauth/authorize")
    def oauth_authorize(request: Request) -> Response:
        params = request.query_params
        session = _session()
        try:
            if params.get("response_type", "code") != "code":
                return Response("unsupported_response_type", status_code=400)
            try:
                row, scopes = validate_authorize(
                    session,
                    params.get("client_id"),
                    params.get("redirect_uri"),
                    params.get("scope"),
                    params.get("code_challenge"),
                    params.get("code_challenge_method"),
                )
            except ValueError as exc:
                return Response(f"invalid_request: {exc}", status_code=400)
            return Response(
                render_template(
                    "oauth_authorize.html",
                    client_name=row.client_name or row.client_id,
                    client_id=row.client_id,
                    redirect_uri=params.get("redirect_uri"),
                    scope_text=" ".join(scopes),
                    scopes=scopes,
                    scope_descriptions=SCOPE_DESCRIPTIONS,
                    state=params.get("state", ""),
                    code_challenge=params.get("code_challenge"),
                    csrf=new_state(session, MCP_CSRF_KEY),
                    needs_profile="profile:read" in scopes,
                    dimensions=DIMENSIONS,
                    error=None,
                ),
                media_type="text/html",
            )
        finally:
            session.close()

    @app.post("/oauth/authorize")
    async def oauth_authorize_submit(request: Request) -> Response:
        form = await request.form()
        client_id = str(form.get("client_id", "") or "")
        redirect_uri = str(form.get("redirect_uri", "") or "")
        session = _session()
        try:
            try:
                row, scopes = validate_authorize(
                    session,
                    client_id,
                    redirect_uri or None,
                    str(form.get("scope", "") or ""),
                    str(form.get("code_challenge", "") or ""),
                    "S256",
                )
            except ValueError as exc:
                return Response(f"invalid_request: {exc}", status_code=400)

            def _deny() -> Response:
                target = redirect_uri + (
                    f"?error=access_denied&state={form.get('state', '')}"
                    if form.get("state")
                    else "?error=access_denied"
                )
                return RedirectResponse(target, status_code=303)

            if str(form.get("decision", "")) != "approve":
                return _deny()
            checked = checked_dimension_codes(form)
            try:
                if not check_state(session, str(form.get("csrf", "") or ""), MCP_CSRF_KEY):
                    raise ValueError("Invalid or expired form - start over from Sources.")
                tenant_db_url = ""
                if tenant_registry_url is not None:
                    from pre.tenants import LoginRequired, open_request_session

                    try:
                        _tenant_session, _tenant = open_request_session(
                            request.cookies, session_factory, tenant_registry_url
                        )
                    except LoginRequired:
                        return RedirectResponse("/auth/google/login", status_code=303)
                    try:
                        tenant_db_url = _tenant.db_url if _tenant is not None else ""
                    finally:
                        _tenant_session.close()
                code = approve_authorize(
                    session,
                    row.client_id,
                    tenant_db_url,
                    redirect_uri,
                    scopes,
                    str(form.get("code_challenge", "") or ""),
                    str(form.get("api_token", "") or "") or None,
                    bool(form.get("allow_profile")),
                    checked,
                )
            except (ValueError, RuntimeError) as exc:
                return Response(
                    render_template(
                        "oauth_authorize.html",
                        client_name=row.client_name or row.client_id,
                        client_id=row.client_id,
                        redirect_uri=redirect_uri,
                        scope_text=" ".join(scopes),
                        scopes=scopes,
                        scope_descriptions=SCOPE_DESCRIPTIONS,
                        state=str(form.get("state", "") or ""),
                        code_challenge=str(form.get("code_challenge", "") or ""),
                        needs_profile="profile:read" in scopes,
                        dimensions=DIMENSIONS,
                        csrf=new_state(session, MCP_CSRF_KEY),
                        error=str(exc),
                    ),
                    status_code=400,
                    media_type="text/html",
                )
            target = redirect_uri + f"?code={code}"
            if form.get("state"):
                target += f"&state={form.get('state')}"
            return RedirectResponse(target, status_code=303)
        finally:
            session.close()

    @app.post("/oauth/token")
    async def oauth_token(request: Request) -> Response:
        form = await request.form()
        grant = str(form.get("grant_type", "") or "")
        client_id = str(form.get("client_id", "") or "")
        secret = str(form.get("client_secret", "") or "") or None
        session = _session()
        try:
            try:
                if grant == "authorization_code":
                    payload = exchange_authorization_code(
                        session,
                        client_id,
                        secret,
                        str(form.get("code", "") or ""),
                        str(form.get("redirect_uri", "") or ""),
                        str(form.get("code_verifier", "") or ""),
                    )
                elif grant == "refresh_token":
                    payload = refresh_access_token(
                        session, client_id, secret, str(form.get("refresh_token", "") or "")
                    )
                else:
                    return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
            except _InvalidClient as exc:
                return JSONResponse({"error": "invalid_client", "detail": str(exc)}, status_code=401)
            except _InvalidGrant as exc:
                return JSONResponse({"error": "invalid_grant", "detail": str(exc)}, status_code=400)
            return JSONResponse(payload)
        finally:
            session.close()
