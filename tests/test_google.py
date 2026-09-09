"""Ticket 23 commit 6: Google OAuth vault, fetchers, and web/CLI surfaces.

No network: a router fake stands in for Google's endpoints; the vault,
parsers, queue, and handlers all run for real.
"""

from __future__ import annotations

import json
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pre.google import (
    CALENDAR_URL,
    GMAIL_LIST_URL,
    GOOGLE_TOKEN_URL,
    GOOGLE_USERINFO_URL,
    check_state,
    exchange_code,
    fetch_account_email,
    fetch_calendar_documents,
    fetch_gmail_documents,
    is_configured,
    new_state,
    store_tokens,
    sync_live_service,
)
from pre.models import OAuthToken
from pre.queue import list_pending


@pytest.fixture()
def token_key(monkeypatch: pytest.MonkeyPatch) -> str:
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode()
    monkeypatch.setenv("PRE_TOKEN_KEY", key)
    return key


class _Resp:
    def __init__(self, payload: object) -> None:
        self._data = json.dumps(payload).encode()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return self._data


def _gmail_msg(sender: str, subject: str) -> dict:
    return {
        "payload": {
            "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
            ]
        }
    }


class _Google:
    """Canned Google: token exchange, userinfo, Gmail list/get, Calendar pages."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.token_payload = {
            "access_token": "AT",
            "refresh_token": "RT",
            "expires_in": 3600,
        }

    def __call__(self, request, timeout: int = 60) -> _Resp:
        url: str = request.full_url
        self.calls.append(f"{request.method} {url.split('?')[0]}")
        body = urllib.parse.parse_qs((request.data or b"").decode())
        if url == GOOGLE_TOKEN_URL:
            grant = body.get("grant_type", [""])[0]
            if grant == "refresh_token":
                return _Resp({**self.token_payload, "access_token": "AT2"})
            return _Resp(dict(self.token_payload))
        if url == GOOGLE_USERINFO_URL:
            return _Resp({"email": "user@example.com"})
        if "/messages/m1" in url:
            return _Resp(_gmail_msg("Ada Quinn <ada@x.co>", "Standup notes"))
        if "/messages/m2" in url:
            return _Resp(_gmail_msg("Ada Quinn <ada@x.co>", "Q3 planning"))
        if url.startswith(GMAIL_LIST_URL):
            return _Resp({"messages": [{"id": "m1"}, {"id": "m2"}]})
        if url.startswith(CALENDAR_URL):
            return _Resp(
                {
                    "items": [
                        {"summary": "Daily standup",
                         "start": {"dateTime": "2026-09-01T09:00:00Z"}},
                        {"summary": "Cancelled thing", "status": "cancelled",
                         "start": {"dateTime": "2026-09-02T09:00:00Z"}},
                        {"start": {"dateTime": "2026-09-03T09:00:00Z"}},
                        {"summary": "Dentist", "start": {"date": "2026-09-04"}},
                    ]
                }
            )
        raise AssertionError(f"unexpected Google call: {url}")


@pytest.fixture()
def google(monkeypatch: pytest.MonkeyPatch) -> _Google:
    fake = _Google()
    monkeypatch.setattr("pre.google.urlopen", fake)
    return fake


@pytest.fixture()
def client(session: Session):
    from fastapi.testclient import TestClient

    from pre.web import create_app

    engine = session.get_bind()

    def factory() -> Session:
        return sessionmaker(bind=engine, expire_on_commit=False)()

    return TestClient(create_app(factory))


def _vault_row(session: Session, token_key: str, hours_valid: int = 1) -> OAuthToken:
    payload = {"access_token": "AT", "refresh_token": "RT", "expires_in": hours_valid * 3600}
    return store_tokens(session, payload, "user@example.com")


def test_missing_token_key_fails_fast(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRE_TOKEN_KEY", raising=False)

    with pytest.raises(RuntimeError, match="PRE_TOKEN_KEY"):
        store_tokens(session, {"access_token": "x"}, "u@x.co")


def test_vault_encrypts_and_serves_fresh_without_http(
    session: Session, token_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pre.google import _valid_access_token

    def _boom(request, timeout: int = 60) -> _Resp:
        raise AssertionError("no HTTP expected for a fresh token")

    row = _vault_row(session, token_key)
    assert "AT" not in row.access_token_enc  # ciphertext at rest
    monkeypatch.setattr("pre.google.urlopen", _boom)

    assert _valid_access_token(session, row) == "AT"


def test_expired_token_refreshes_in_place(
    session: Session, token_key: str, google: _Google
) -> None:
    from pre.google import _valid_access_token

    row = _vault_row(session, token_key)
    row.expires_at = datetime.now(UTC) - timedelta(hours=1)
    session.commit()

    assert _valid_access_token(session, row) == "AT2"
    session.expire_all()
    calls = len(google.calls)
    stored = session.scalar(select(OAuthToken))
    assert stored is not None
    assert _valid_access_token(session, stored) == "AT2"
    assert len(google.calls) == calls  # second serve comes from the refreshed row


def test_exchange_rejects_empty_payload(
    session: Session, token_key: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _Empty(_Google):
        def __call__(self, request, timeout: int = 60) -> _Resp:
            return _Resp({})

    monkeypatch.setattr("pre.google.urlopen", _Empty())

    with pytest.raises(ValueError, match="access token"):
        exchange_code("bogus")


def test_state_nonce_single_use(session: Session) -> None:
    nonce = new_state(session)

    assert check_state(session, nonce) is True
    assert check_state(session, nonce) is False
    assert check_state(session, "wrong") is False


def test_is_configured_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRE_GOOGLE_CLIENT_ID", raising=False)
    assert is_configured() is False
    monkeypatch.setenv("PRE_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("PRE_GOOGLE_CLIENT_SECRET", "shh")
    assert is_configured() is True


def test_fetch_gmail_builds_canonical_docs(
    session: Session, token_key: str, google: _Google
) -> None:
    row = _vault_row(session, token_key)

    docs = fetch_gmail_documents(session, row)

    assert {"from": "Ada Quinn <ada@x.co>", "subject": "Standup notes"} in docs
    assert len(docs) == 2


def test_fetch_calendar_skips_cancelled_and_untitled(
    session: Session, token_key: str, google: _Google
) -> None:
    row = _vault_row(session, token_key)

    docs = fetch_calendar_documents(session, row)

    titles = {d["title"] for d in docs}
    assert {"Daily standup", "Dentist"} <= titles
    assert "Cancelled thing" not in titles
    assert all(d["title"] for d in docs)


def test_sync_live_end_to_end_offline(
    session: Session, token_key: str, google: _Google
) -> None:
    _vault_row(session, token_key)

    summary = sync_live_service(session, "email")

    assert summary["documents"] == 2
    assert summary["proposals_new"] >= 1
    assert {p.entity_type for p in list_pending(session)} >= {"person"}

    with pytest.raises(ValueError, match="unknown live service"):
        sync_live_service(session, "sms")


def test_sync_without_account_fails_readably(session: Session) -> None:
    with pytest.raises(RuntimeError, match="no Google account connected"):
        sync_live_service(session, "email")


def test_fetch_account_email(session: Session, token_key: str, google: _Google) -> None:
    assert fetch_account_email("AT") == "user@example.com"


def test_connect_redirects_to_google(
    client, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRE_GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("PRE_GOOGLE_CLIENT_SECRET", "shh")

    response = client.get("/sources/connect/google", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("https://accounts.google.com/")


def test_connect_unconfigured_renders_error(client) -> None:
    response = client.get("/sources/connect/google")

    assert response.status_code == 400
    assert "not configured" in response.text


def test_callback_denied_renders_error(client) -> None:
    response = client.get("/sources/oauth/google/callback?error=access_denied")

    assert response.status_code == 400
    assert "denied" in response.text


def test_callback_bad_state_rejected(client, session: Session) -> None:
    response = client.get("/sources/oauth/google/callback?code=x&state=y")

    assert response.status_code == 400
    assert "Invalid OAuth state" in response.text


def test_disconnect_removes_vault_row(
    client, session: Session, token_key: str
) -> None:
    _vault_row(session, token_key)

    response = client.post("/sources/disconnect/google", follow_redirects=False)

    assert response.status_code == 303
    assert session.query(OAuthToken).count() == 0


def test_cli_sync_live_without_account(tmp_path: Path) -> None:
    from pre.cli import main

    url = f"sqlite:///{tmp_path / 'cli.db'}"

    assert main(["--db", url, "sync-live"]) == 1
