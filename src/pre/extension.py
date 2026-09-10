"""Browser extension lane (issue 26): consent-gated page capture into the corpus.

A captured page becomes a corpus Change through the shared `ingest_entries`
seam, so fingerprint dedup against aggregator lanes comes free: capturing an
already-known Change only appends this lane to its sources. The rules that
are specific to automatic capture — master switch, per-host blocklist,
same-URL-once noise policy — live here, never in the corpus.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.change_corpus import FirehoseEntry, ingest_entries
from pre.models import Change, DigestItem, SystemFlag

BROWSER_SOURCE = "browser-capture"
CAPTURE_CONSENT_MASTER = "browser_capture"
CAPTURE_BLOCKED_HOSTS = "browser_capture_blocked"
MIN_TITLE_LEN = 3
MAX_TITLE_LEN = 256


@dataclass(frozen=True)
class CaptureOutcome:
    outcome: str  # 'created' | 'deduped' | 'strengthened'
    product: str


def host_of(url: str) -> str:
    """Lowercase page host, or "" when the URL is not http(s)."""
    try:
        parts = urllib.parse.urlparse(url.strip())
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    return parts.hostname.lower()


def product_for_url(url: str) -> str:
    """Best-guess product name from a page URL (second-level host label)."""
    host = host_of(url)
    if not host:
        return ""
    labels = [part for part in host.split(".") if part and part != "www"]
    if not labels or all(part.isdigit() for part in labels):
        return host
    return labels[-2] if len(labels) >= 2 else labels[0]


def normalize_url(url: str) -> str:
    """Canonical page URL: lower scheme+host, no fragment, no trailing slash."""
    host = host_of(url)
    if not host:
        return ""
    parts = urllib.parse.urlparse(url.strip())
    path = parts.path.rstrip("/") if parts.path != "/" else ""
    query = f"?{parts.query}" if parts.query else ""
    return f"{parts.scheme.lower()}://{host}{path}{query}"


def get_consent(session: Session) -> tuple[bool, set[str]]:
    """(master switch, blocked hosts). Default: capture disabled."""
    master = session.scalar(select(SystemFlag).where(SystemFlag.key == CAPTURE_CONSENT_MASTER))
    if master is None or master.value != "1":
        return False, set()
    blocked = session.scalar(select(SystemFlag).where(SystemFlag.key == CAPTURE_BLOCKED_HOSTS))
    if blocked is None or not blocked.value:
        return True, set()
    return True, {h.strip().lower() for h in blocked.value.split(",") if h.strip()}


def set_consent(session: Session, enabled: bool, blocked_hosts: set[str]) -> None:
    """Persist capture consent. Blocked hosts compare case-insensitively."""
    cleaned = ",".join(sorted({h.strip().lower() for h in blocked_hosts if h.strip()}))
    for key, value in ((CAPTURE_CONSENT_MASTER, "1" if enabled else "0"),
                       (CAPTURE_BLOCKED_HOSTS, cleaned)):
        flag = session.scalar(select(SystemFlag).where(SystemFlag.key == key))
        if flag is None:
            session.add(SystemFlag(key=key, value=value))
        else:
            flag.value = value
    session.commit()


def build_entry(url: str, title: str) -> FirehoseEntry:
    """Validate one capture into a corpus entry. Raises ValueError on bad input."""
    normalized = normalize_url(url)
    if not normalized:
        raise ValueError(f"not a capturable page URL: {url!r}")
    clean = " ".join(title.split())
    if len(clean) < MIN_TITLE_LEN:
        raise ValueError("capture needs a page title")
    host = host_of(url)
    return FirehoseEntry(
        product_name=product_for_url(url) or host,
        title=clean[:MAX_TITLE_LEN],
        url=normalized,
    )


def record_capture(session: Session, url: str, title: str) -> CaptureOutcome:
    """Capture one page: consent-gated, same-URL-once, corpus-deduped.

    Raises PermissionError when capture is disabled or the host is blocked,
    ValueError on bad input.
    """
    entry = build_entry(url, title)
    enabled, blocked = get_consent(session)
    if not enabled:
        raise PermissionError("browser capture is disabled for this tenant")
    if host_of(url) in blocked:
        raise PermissionError(f"capture from {host_of(url)} is blocked")
    assert entry.url is not None
    seen = session.scalar(select(Change).where(Change.url == entry.url))
    if seen is not None:
        return CaptureOutcome(outcome="deduped", product=entry.product_name)
    result = ingest_entries(session, [entry], BROWSER_SOURCE)
    return CaptureOutcome(
        outcome="created" if result.created else "strengthened",
        product=entry.product_name,
    )


def overlay_matches(session: Session, url: str) -> list[dict[str, object]]:
    """Digest items whose Change lives on the visited page's host, best first."""
    host = host_of(url)
    if not host:
        return []
    matches = []
    items = session.scalars(select(DigestItem).order_by(DigestItem.score.desc())).all()
    for item in items:
        change = session.get(Change, item.change_id)
        if change is None or not change.url or host_of(change.url) != host:
            continue
        matches.append(
            {
                "item_id": item.id,
                "product": change.product_name,
                "title": change.title,
                "score": item.score,
                "verdict": item.verdict,
            }
        )
    return matches


__all__ = [
    "BROWSER_SOURCE",
    "CAPTURE_BLOCKED_HOSTS",
    "CAPTURE_CONSENT_MASTER",
    "CaptureOutcome",
    "build_entry",
    "get_consent",
    "host_of",
    "normalize_url",
    "overlay_matches",
    "product_for_url",
    "record_capture",
    "set_consent",
]
