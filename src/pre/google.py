"""Google OAuth + live fetchers (issue 23 commit 6).

Browser OAuth (consent once) replaces hand-rolled API keys: tokens rest
Fernet-encrypted in the vault — master key from PRE_TOKEN_KEY, generated with
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`,
exported in the deployment environment. Secrets are never logged or rendered.

Fetchers produce the canonical JSON documents live.py already parses, so the
fetch step swaps without touching parsing, queueing, or auto-accept. Gmail
paginates message ids fully but caps full fetches at MAX_MESSAGES recent
(doctrine note: backfill breadth is bounded by API cost, deltas stay exact);
Calendar follows pages fully. `pre sync-live` (cron entry point) syncs every
connected service through the normal import pipeline.
"""

from __future__ import annotations

import dataclasses
import json
import os
import secrets
import tempfile
import urllib.parse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import OAuthToken, SystemFlag

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GMAIL_LIST_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages"
GMAIL_GET_URL = (
    "https://gmail.googleapis.com/gmail/v1/users/me/messages/"
    "{id}?format=metadata&metadataHeaders=From&metadataHeaders=Subject"
)
CALENDAR_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
]
STATE_KEY = "oauth_state_google"
MAX_MESSAGES = 500


def _client_id() -> str:
    return os.environ.get("PRE_GOOGLE_CLIENT_ID", "")


def _client_secret() -> str:
    return os.environ.get("PRE_GOOGLE_CLIENT_SECRET", "")


def redirect_url() -> str:
    return os.environ.get(
        "PRE_GOOGLE_REDIRECT_URL",
        "http://127.0.0.1:8787/sources/oauth/google/callback",
    )


def is_configured() -> bool:
    """True when a Google OAuth client is registered (id + secret present)."""
    return bool(_client_id() and _client_secret())


def _fernet() -> Fernet:
    raw = os.environ.get("PRE_TOKEN_KEY", "")
    if not raw:
        raise RuntimeError(
            "PRE_TOKEN_KEY is not set; generate one and export it in deployment"
        )
    try:
        return Fernet(raw.encode("utf-8"))
    except ValueError as exc:
        raise RuntimeError("PRE_TOKEN_KEY must be a Fernet key") from exc


def _seal(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def _open(blob: str) -> str:
    try:
        return _fernet().decrypt(blob.encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise RuntimeError("cannot decrypt stored token — PRE_TOKEN_KEY changed?") from exc


def _http_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    data: dict[str, str] | None = None,
) -> Any:
    body = urllib.parse.urlencode(data).encode() if data is not None else None
    request = Request(url, data=body, headers=headers or {}, method=method)
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def authorization_url(state: str) -> str:
    params = urllib.parse.urlencode(
        {
            "client_id": _client_id(),
            "redirect_uri": redirect_url(),
            "response_type": "code",
            "scope": " ".join(SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return f"{GOOGLE_AUTH_URL}?{params}"


def new_state(session: Session, key: str = STATE_KEY) -> str:
    """Mint a CSRF nonce for an OAuth round-trip (single-use, checked on return)."""
    token = secrets.token_urlsafe(24)
    flag = session.scalar(select(SystemFlag).where(SystemFlag.key == key))
    if flag is None:
        session.add(SystemFlag(key=key, value=token))
    else:
        flag.value = token
    session.commit()
    return token


def check_state(session: Session, state: str | None, key: str = STATE_KEY) -> bool:
    """Single-use CSRF check: compares then burns the nonce either way."""
    flag = session.scalar(select(SystemFlag).where(SystemFlag.key == key))
    ok = flag is not None and secrets.compare_digest(flag.value, state or "")
    if flag is not None:
        session.delete(flag)
        session.commit()
    return ok


def exchange_code(code: str) -> dict[str, Any]:
    payload = _http_json(
        "POST",
        GOOGLE_TOKEN_URL,
        data={
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_url(),
        },
    )
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise ValueError("Google did not return an access token")
    return payload


def fetch_account_email(access_token: str) -> str:
    info = _http_json(
        "GET", GOOGLE_USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"}
    )
    email = str(info.get("email", "")).strip() if isinstance(info, dict) else ""
    if not email:
        raise ValueError("Google account has no email address")
    return email


def store_tokens(
    session: Session, token_payload: dict[str, Any], account_email: str
) -> OAuthToken:
    """Upsert the Google vault row (v1: one account); keeps the old refresh token
    when a re-consent omits it."""
    row = session.scalar(select(OAuthToken).where(OAuthToken.service == "google"))
    if row is None:
        row = OAuthToken(service="google", account_email=account_email)
        session.add(row)
    else:
        row.account_email = account_email
    row.access_token_enc = _seal(str(token_payload["access_token"]))
    if token_payload.get("refresh_token"):
        row.refresh_token_enc = _seal(str(token_payload["refresh_token"]))
    lifetime = int(token_payload.get("expires_in", 3600) or 3600)
    row.expires_at = datetime.now(UTC) + timedelta(seconds=lifetime)
    session.commit()
    return row


def _naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


def _valid_access_token(session: Session, row: OAuthToken) -> str:
    """Fresh access token, refreshing in place when within a minute of expiry."""
    fresh_until = None
    if row.expires_at is not None:
        fresh_until = _naive(row.expires_at) - timedelta(seconds=60)
    if fresh_until is not None and _naive(datetime.now(UTC)) < fresh_until:
        return _open(row.access_token_enc)
    payload = _http_json(
        "POST",
        GOOGLE_TOKEN_URL,
        data={
            "client_id": _client_id(),
            "client_secret": _client_secret(),
            "refresh_token": _open(row.refresh_token_enc),
            "grant_type": "refresh_token",
        },
    )
    if not isinstance(payload, dict) or not payload.get("access_token"):
        raise ValueError("Google refused the refresh token")
    row.access_token_enc = _seal(str(payload["access_token"]))
    lifetime = int(payload.get("expires_in", 3600) or 3600)
    row.expires_at = datetime.now(UTC) + timedelta(seconds=lifetime)
    session.commit()
    return str(payload["access_token"])


def fetch_gmail_documents(
    session: Session, row: OAuthToken, max_messages: int = MAX_MESSAGES
) -> list[dict[str, Any]]:
    """Recent mailbox -> canonical email documents ({from, subject})."""
    token = _valid_access_token(session, row)
    headers = {"Authorization": f"Bearer {token}"}
    ids: list[str] = []
    page: str | None = None
    while len(ids) < max_messages:
        url = f"{GMAIL_LIST_URL}?maxResults={min(100, max_messages - len(ids))}"
        if page:
            url += f"&pageToken={urllib.parse.quote(page)}"
        listing = _http_json("GET", url, headers=headers)
        ids.extend(entry["id"] for entry in listing.get("messages", []) if entry.get("id"))
        page = listing.get("nextPageToken")
        if not page:
            break
    docs = []
    for message_id in ids[:max_messages]:
        full = _http_json("GET", GMAIL_GET_URL.format(id=message_id), headers=headers)
        found = {
            str(h.get("name", "")).lower(): str(h.get("value", ""))
            for h in full.get("payload", {}).get("headers", [])
        }
        docs.append({"from": found.get("from", ""), "subject": found.get("subject", "")})
    return docs


def fetch_calendar_documents(session: Session, row: OAuthToken) -> list[dict[str, Any]]:
    """Calendar event titles -> canonical calendar documents ({title, date})."""
    token = _valid_access_token(session, row)
    headers = {"Authorization": f"Bearer {token}"}
    docs: list[dict[str, Any]] = []
    page: str | None = None
    while True:
        params = urllib.parse.urlencode(
            {
                "singleEvents": "true",
                "orderBy": "startTime",
                "maxResults": "250",
                "timeMin": "2000-01-01T00:00:00Z",
                **({"pageToken": page} if page else {}),
            }
        )
        data = _http_json("GET", f"{CALENDAR_URL}?{params}", headers=headers)
        for event in data.get("items", []):
            title = str(event.get("summary", "")).strip()
            if not title or str(event.get("status", "")) == "cancelled":
                continue
            start = event.get("start", {}) or {}
            docs.append(
                {"title": title, "date": str(start.get("dateTime", "") or start.get("date", ""))}
            )
        page = data.get("nextPageToken")
        if not page:
            break
    return docs


def cache_path(kind: str) -> Path:
    base = Path(os.environ.get("PRE_CACHE_DIR", "") or Path(tempfile.gettempdir()) / "pre-cache")
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{kind}.json"


def sync_live_service(session: Session, kind: str) -> dict[str, Any]:
    """Fetch one live service via OAuth, then import through the normal pipeline."""
    if kind == "calendar":
        fetch, docs_kind = fetch_calendar_documents, "calendar"
    elif kind == "email":
        fetch, docs_kind = fetch_gmail_documents, "email"
    else:
        raise ValueError(f"unknown live service {kind!r}; expected calendar or email")
    row = session.scalar(select(OAuthToken).where(OAuthToken.service == "google"))
    if row is None:
        raise RuntimeError("no Google account connected — connect one from Sources first")
    docs = fetch(session, row)
    dest = cache_path(kind)
    dest.write_text(json.dumps(docs), encoding="utf-8")

    from pre.ingest import import_file

    result = import_file(session, docs_kind, dest)
    return {"kind": kind, "documents": len(docs), **dataclasses.asdict(result)}


__all__ = [
    "CALENDAR_URL",
    "GMAIL_GET_URL",
    "GMAIL_LIST_URL",
    "GOOGLE_AUTH_URL",
    "GOOGLE_TOKEN_URL",
    "GOOGLE_USERINFO_URL",
    "MAX_MESSAGES",
    "SCOPES",
    "STATE_KEY",
    "authorization_url",
    "cache_path",
    "check_state",
    "exchange_code",
    "fetch_account_email",
    "fetch_calendar_documents",
    "fetch_gmail_documents",
    "is_configured",
    "new_state",
    "redirect_url",
    "store_tokens",
    "sync_live_service",
]
