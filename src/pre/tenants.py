"""SaaS tenancy: registry, Google login, provisioning, per-request routing (issue 25).

Control plane (who) lives in the registry database; data plane (whose life)
lives in per-tenant databases. Browser sessions are opaque random tokens —
revocation is a row delete. Google sign-in reuses the ticket-23 OAuth client
(same Cloud app, login redirect); only the redirect URI and scopes differ.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import urllib.parse
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from pre.db import init_db, init_registry, make_engine, make_session_factory
from pre.google import GOOGLE_TOKEN_URL, GOOGLE_USERINFO_URL
from pre.models import Tenant, TenantSession

SESSION_COOKIE = "pre_session"
SESSION_TTL_DAYS = 30
LOGIN_SCOPES = ["openid", "email"]

_TENANT_ENGINES: dict[str, Engine] = {}
_REGISTRY_ENGINES: dict[str, Engine] = {}


class LoginRequired(Exception):
    """No usable tenant session; pages redirect to login, APIs answer 401."""


def clear_engine_cache() -> None:
    """Dispose every cached engine (tests; tenants hold file locks otherwise)."""
    for engine in (*_TENANT_ENGINES.values(), *_REGISTRY_ENGINES.values()):
        engine.dispose()
    _TENANT_ENGINES.clear()
    _REGISTRY_ENGINES.clear()


def get_engine(db_url: str) -> Engine:
    """Tenant database engine, created (tables included) on first use."""
    engine = _TENANT_ENGINES.get(db_url)
    if engine is None:
        engine = make_engine(db_url)
        init_db(engine)
        _TENANT_ENGINES[db_url] = engine
    return engine


def get_registry(registry_url: str) -> Engine:
    """Registry database engine, created (tables included) on first use."""
    engine = _REGISTRY_ENGINES.get(registry_url)
    if engine is None:
        engine = make_engine(registry_url)
        init_registry(engine)
        _REGISTRY_ENGINES[registry_url] = engine
    return engine


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


def provision_sqlite_tenant(base_dir: str | Path, email: str) -> str:
    """Allocate a tenant database file. Stable, collision-free, absolute URL."""
    slug = re.sub(r"[^a-z0-9]+", "-", email.lower()).strip("-") or "tenant"
    stamp = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:8]
    path = Path(base_dir).expanduser().resolve() / f"{slug}-{stamp}.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


def register_tenant(session: Session, email: str, db_url: str) -> Tenant:
    """Get-or-create a tenant row. Existing rows win (never silently repointed)."""
    normalized = email.strip().lower()
    if not normalized:
        raise ValueError("email is required")
    row = session.scalar(select(Tenant).where(Tenant.email == normalized))
    if row is None:
        row = Tenant(email=normalized, db_url=db_url)
        session.add(row)
        session.commit()
    return row


def create_session_token(session: Session, tenant_id: int, ttl_days: int = SESSION_TTL_DAYS) -> str:
    """Mint an opaque browser session token (plaintext returned once)."""
    token = secrets.token_urlsafe(32)
    session.add(
        TenantSession(
            tenant_id=tenant_id,
            token_hash=_digest(token),
            expires_at=datetime.now(UTC) + timedelta(days=ttl_days),
        )
    )
    session.commit()
    return token


def destroy_session(session: Session, token: str) -> None:
    row = session.scalar(select(TenantSession).where(TenantSession.token_hash == _digest(token)))
    if row is not None:
        session.delete(row)
        session.commit()


def resolve_session(session: Session, token: str) -> Tenant | None:
    """The tenant behind a session token, or None (unknown/expired/blank)."""
    if not token:
        return None
    row = session.scalar(select(TenantSession).where(TenantSession.token_hash == _digest(token)))
    if row is None or _naive(datetime.now(UTC)) > _naive(row.expires_at):
        return None
    return session.get(Tenant, row.tenant_id)


def effective_cap_cents(tenant: Tenant | None) -> int:
    """Monthly LLM cap for one tenant: personal override wins, else the default."""
    from pre.cost_meter import monthly_cap_cents

    if tenant is not None and tenant.cap_override_cents:
        return tenant.cap_override_cents
    return monthly_cap_cents()


def cap_for_session(session: Session, registry_url: str | None = None) -> int:
    """Monthly cap for the database behind this session.

    Multi-tenant mode (registry configured) with a matching tenant row: that
    tenant's override wins. Anything else: the global default (legacy intact).
    """
    from pre.cost_meter import monthly_cap_cents

    if registry_url is None:
        registry_url = os.environ.get("TENANT_REGISTRY_URL", "") or None
    bind = session.get_bind()
    url = str(bind.url) if isinstance(bind, Engine) else ""
    if not registry_url or not url:
        return monthly_cap_cents()
    registry = make_session_factory(get_registry(registry_url))()
    try:
        tenant = registry.scalar(select(Tenant).where(Tenant.db_url == url))
    finally:
        registry.close()
    return effective_cap_cents(tenant)


def open_request_session(
    cookies: Mapping[str, str],
    default_factory: Callable[[], Session],
    registry_url: str | None,
) -> tuple[Session, Tenant | None]:
    """Open the right database for one request.

    No registry configured: the legacy single database (existing behavior,
    existing tests). Registry configured: the cookie's tenant database, or
    LoginRequired when the cookie is missing, unknown, or expired.
    """
    if not registry_url:
        return default_factory(), None
    engine = get_registry(registry_url)
    factory = make_session_factory(engine)
    registry = factory()
    try:
        tenant = resolve_session(registry, cookies.get(SESSION_COOKIE, ""))
    finally:
        registry.close()
    if tenant is None:
        raise LoginRequired("tenant login required")
    return make_session_factory(get_engine(tenant.db_url))(), tenant


def login_redirect_url() -> str:
    return os.environ.get(
        "PRE_GOOGLE_LOGIN_REDIRECT_URL",
        "http://127.0.0.1:8787/auth/google/callback",
    )


def login_authorization_url(state: str) -> str:
    params = urllib.parse.urlencode(
        {
            "client_id": os.environ.get("PRE_GOOGLE_CLIENT_ID", ""),
            "redirect_uri": login_redirect_url(),
            "response_type": "code",
            "scope": " ".join(LOGIN_SCOPES),
            "state": state,
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


def login_exchange(code: str) -> str:
    """Exchange a login code for the Google account email. Raises on any failure."""
    token_request = Request(
        GOOGLE_TOKEN_URL,
        data=urllib.parse.urlencode(
            {
                "client_id": os.environ.get("PRE_GOOGLE_CLIENT_ID", ""),
                "client_secret": os.environ.get("PRE_GOOGLE_CLIENT_SECRET", ""),
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": login_redirect_url(),
            }
        ).encode(),
        method="POST",
    )
    with urlopen(token_request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise ValueError("Google did not return an access token")
    info_request = Request(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {payload['access_token']}"},
        method="GET",
    )
    with urlopen(info_request, timeout=60) as response:
        info = json.loads(response.read().decode("utf-8"))
    email = str(info.get("email", "")).strip() if isinstance(info, dict) else ""
    if not email:
        raise ValueError("Google account has no email address")
    return email


__all__ = [
    "LOGIN_SCOPES",
    "SESSION_COOKIE",
    "SESSION_TTL_DAYS",
    "LoginRequired",
    "Tenant",
    "cap_for_session",
    "clear_engine_cache",
    "create_session_token",
    "destroy_session",
    "get_engine",
    "get_registry",
    "login_authorization_url",
    "login_exchange",
    "login_redirect_url",
    "open_request_session",
    "provision_sqlite_tenant",
    "register_tenant",
    "resolve_session",
]
