"""Ticket 24 commit 1: self-issued OAuth issuer. Fully offline — no network."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pre.models import MCPToken, SystemFlag

VERIFIER = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"  # RFC 7636 vector


@pytest.fixture()
def client(session: Session):
    from fastapi.testclient import TestClient

    from pre.web import create_app

    engine = session.get_bind()

    def factory() -> Session:
        return sessionmaker(bind=engine, expire_on_commit=False)()

    return TestClient(create_app(factory))


@pytest.fixture()
def authed_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PRE_API_TOKEN", "opensesame")
    return "opensesame"


def _register(client, scope: str = "digest:read verdict:write") -> dict:
    response = client.post(
        "/oauth/register",
        json={
            "redirect_uris": ["http://localhost:9999/cb"],
            "client_name": "Test Assistant",
            "scope": scope,
        },
    )
    assert response.status_code == 201
    return response.json()


def _authorize_params(reg: dict, **overrides: str) -> dict:
    params = {
        "response_type": "code",
        "client_id": reg["client_id"],
        "redirect_uri": "http://localhost:9999/cb",
        "scope": "digest:read",
        "state": "xyz",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
    }
    params.update(overrides)
    return params


def _approve(client, reg: dict, token: str, **extra: str):
    import re

    scope = extra.get("scope", "digest:read")
    page = client.get(
        "/oauth/authorize",
        params=_authorize_params(reg, scope=scope),
    )
    assert page.status_code == 200
    nonce = re.search(r"name='csrf' value='([^']+)'", page.text).group(1)
    form = {
        "client_id": reg["client_id"],
        "redirect_uri": "http://localhost:9999/cb",
        "scope": "digest:read",
        "state": "xyz",
        "code_challenge": CHALLENGE,
        "decision": "approve",
        "api_token": token,
        "csrf": nonce,
    }
    form.update(extra)
    return client.post("/oauth/authorize", data=form, follow_redirects=False)


def test_approve_rejects_missing_and_wrong_nonce(client, authed_env: str) -> None:
    reg = _register(client)
    base = {
        "client_id": reg["client_id"],
        "redirect_uri": "http://localhost:9999/cb",
        "scope": "digest:read",
        "state": "xyz",
        "code_challenge": CHALLENGE,
        "decision": "approve",
        "api_token": authed_env,
    }

    assert client.post("/oauth/authorize", data=base).status_code == 400
    assert client.post(
        "/oauth/authorize", data={**base, "csrf": "bogus"}
    ).status_code == 400


def test_register_and_metadata(client) -> None:
    reg = _register(client)

    assert reg["client_secret"]
    assert reg["scope"] == "digest:read verdict:write"

    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["registration_endpoint"].endswith("/oauth/register")
    assert "S256" in meta["code_challenge_methods_supported"]
    assert meta["token_endpoint_auth_methods_supported"] == ["client_secret_post"]
    protected = client.get("/.well-known/oauth-protected-resource").json()
    assert protected["resource"].endswith("/mcp")
    assert "digest:read" in protected["scopes_supported"]


def test_register_rejects_bad_input(client) -> None:
    bad_uri = client.post("/oauth/register", json={"redirect_uris": ["http://evil.com/cb"]})
    assert bad_uri.status_code == 400
    bad_scope = client.post(
        "/oauth/register",
        json={"redirect_uris": ["http://localhost:1/cb"], "scope": "root:all"},
    )
    assert bad_scope.status_code == 400


def test_authorize_page_and_validation(client) -> None:
    reg = _register(client)

    page = client.get("/oauth/authorize", params=_authorize_params(reg))
    assert page.status_code == 200
    assert "Test Assistant" in page.text

    assert client.get("/oauth/authorize", params=_authorize_params(reg, client_id="nope")).status_code == 400
    assert client.get(
        "/oauth/authorize", params=_authorize_params(reg, redirect_uri="http://evil.com/cb")
    ).status_code == 400
    assert client.get(
        "/oauth/authorize", params={**_authorize_params(reg), "code_challenge": ""}
    ).status_code == 400


def test_approve_needs_configured_matching_token(client, session: Session) -> None:
    reg = _register(client)

    response = _approve(client, reg, "whatever")
    assert response.status_code == 400  # no PRE_API_TOKEN configured
    assert "no API token" in response.text


def test_deny_redirects_with_error(client, authed_env: str) -> None:
    reg = _register(client)

    response = client.post(
        "/oauth/authorize",
        data={
            "client_id": reg["client_id"],
            "redirect_uri": "http://localhost:9999/cb",
            "scope": "digest:read",
            "state": "xyz",
            "code_challenge": CHALLENGE,
            "decision": "deny",
            "api_token": authed_env,
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "error=access_denied" in response.headers["location"]


def _full_grant(client, authed_env: str) -> dict:
    reg = _register(client)
    approval = _approve(client, reg, authed_env)
    assert approval.status_code == 303
    code = dict(
        part.split("=", 1)
        for part in approval.headers["location"].split("?", 1)[1].split("&")
    )["code"]
    token = client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": reg["client_id"],
            "client_secret": reg["client_secret"],
            "code": code,
            "redirect_uri": "http://localhost:9999/cb",
            "code_verifier": VERIFIER,
        },
    )
    assert token.status_code == 200
    return {"reg": reg, **token.json()}


def test_code_exchange_and_single_use(client, session: Session, authed_env: str) -> None:
    grant = _full_grant(client, authed_env)

    assert grant["token_type"] == "Bearer"
    assert grant["refresh_token"]
    assert grant["scope"] == "digest:read"
    row = session.scalars(select(MCPToken)).one()
    assert grant["access_token"] not in row.access_hash  # hashes at rest
    assert row.access_hash != row.refresh_hash


def test_code_replay_and_bad_verifier_rejected(
    client, session: Session, authed_env: str
) -> None:
    reg = _register(client)
    approval = _approve(client, reg, authed_env)
    code = approval.headers["location"].split("code=")[1].split("&")[0]
    good = {
        "grant_type": "authorization_code",
        "client_id": reg["client_id"],
        "client_secret": reg["client_secret"],
        "code": code,
        "redirect_uri": "http://localhost:9999/cb",
        "code_verifier": VERIFIER,
    }
    assert client.post("/oauth/token", data=good).status_code == 200
    replay = client.post("/oauth/token", data=good)
    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"

    approval2 = _approve(client, reg, authed_env)
    code2 = approval2.headers["location"].split("code=")[1].split("&")[0]
    bad_verifier = {**good, "code": code2, "code_verifier": "wrong"}
    assert client.post("/oauth/token", data=bad_verifier).status_code == 400


def test_refresh_rotates_and_expires(
    client, session: Session, authed_env: str
) -> None:
    grant = _full_grant(client, authed_env)

    first = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": grant["reg"]["client_id"],
            "client_secret": grant["reg"]["client_secret"],
            "refresh_token": grant["refresh_token"],
        },
    )
    assert first.status_code == 200
    assert first.json()["access_token"] != grant["access_token"]

    replay = client.post(
        "/oauth/token",
        data={
            "grant_type": "refresh_token",
            "client_id": grant["reg"]["client_id"],
            "client_secret": grant["reg"]["client_secret"],
            "refresh_token": grant["refresh_token"],
        },
    )
    assert replay.status_code == 400  # rotated refresh token is dead


def test_consent_flags_recorded_on_profile_grant(
    client, session: Session, authed_env: str
) -> None:
    reg = _register(client, scope="digest:read profile:read")
    response = _approve(
        client,
        reg,
        authed_env,
        scope="digest:read profile:read",
        allow_profile="1",
        dim_business="1",
    )
    assert response.status_code == 303

    master = session.scalar(select(SystemFlag).where(SystemFlag.key == "mcp_consent_master"))
    dims = session.scalar(select(SystemFlag).where(SystemFlag.key == "mcp_consent_dimensions"))
    assert master is not None and master.value == "1"
    assert dims is not None and dims.value == "business"


def test_vault_verifier_accepts_rejects_expires(
    client, session: Session, authed_env: str
) -> None:
    from pre.mcp_oauth import VaultVerifier

    grant = _full_grant(client, authed_env)
    engine = session.get_bind()
    verifier = VaultVerifier(
        lambda: sessionmaker(bind=engine, expire_on_commit=False)()
    )

    good = asyncio.run(verifier.verify_token(grant["access_token"]))
    assert good is not None and good.client_id == grant["reg"]["client_id"]
    assert asyncio.run(verifier.verify_token("bogus")) is None

    row = session.scalars(select(MCPToken)).one()
    from datetime import UTC, datetime, timedelta

    row.access_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.commit()
    session.expire_all()
    assert asyncio.run(verifier.verify_token(grant["access_token"])) is None
