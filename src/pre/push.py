"""Per-tenant web push (ticket 27): VAPID delivery with quiet hours.

Subscriptions rest in the tenant database (per-tenant by placement). Sending
is injectable: cron passes nothing (pywebpush delivers), tests pass a fake.
Dead endpoints (410/404) are pruned on sight; delivery inside quiet hours
waits for morning; repeat runs stay silent until new items assemble.
"""

from __future__ import annotations

import json
import os
from base64 import urlsafe_b64encode
from collections.abc import Callable, Mapping
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import DigestItem, PushSubscription, SystemFlag

QUIET_START_KEY = "push_quiet_start"
QUIET_END_KEY = "push_quiet_end"
LAST_NOTIFY_KEY = "push_last_notify"
DEFAULT_QUIET_START = 22
DEFAULT_QUIET_END = 7

Sender = Callable[[PushSubscription, Mapping[str, object]], None]


def _naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


def _get_flag(session: Session, key: str) -> str | None:
    flag = session.scalar(select(SystemFlag).where(SystemFlag.key == key))
    return flag.value if flag is not None else None


def _set_flag(session: Session, key: str, value: str) -> None:
    flag = session.scalar(select(SystemFlag).where(SystemFlag.key == key))
    if flag is None:
        session.add(SystemFlag(key=key, value=value))
    else:
        flag.value = value


def get_quiet_hours(session: Session) -> tuple[int, int]:
    """Per-tenant nightly window (UTC hours). Default 22:00-07:00."""
    try:
        start = int(_get_flag(session, QUIET_START_KEY) or DEFAULT_QUIET_START)
        end = int(_get_flag(session, QUIET_END_KEY) or DEFAULT_QUIET_END)
    except ValueError:
        return DEFAULT_QUIET_START, DEFAULT_QUIET_END
    return start, end


def set_quiet_hours(session: Session, start: int, end: int) -> None:
    """Persist the nightly window. Hours are 0-23 UTC."""
    for hour in (start, end):
        if not 0 <= hour <= 23:
            raise ValueError(f"quiet hour must be 0-23 (got {hour})")
    _set_flag(session, QUIET_START_KEY, str(start))
    _set_flag(session, QUIET_END_KEY, str(end))
    session.commit()


def in_quiet_hours(session: Session, now: datetime | None = None) -> bool:
    """Whether delivery should wait for morning (overnight windows wrap)."""
    start, end = get_quiet_hours(session)
    hour = (_naive(now) if now is not None else _naive(datetime.now(UTC))).hour
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def add_subscription(session: Session, endpoint: str, keys: Mapping[str, str]) -> PushSubscription:
    """Register a browser endpoint. Re-subscribing refreshes keys, never duplicates."""
    if not endpoint.startswith("https://"):
        raise ValueError("subscription endpoint must be an https URL")
    row = session.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if row is None:
        row = PushSubscription(endpoint=endpoint)
        session.add(row)
    row.p256dh = str(keys.get("p256dh", "") or "")
    row.auth = str(keys.get("auth", "") or "")
    session.commit()
    return row


def remove_subscription(session: Session, endpoint: str) -> None:
    row = session.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if row is not None:
        session.delete(row)
        session.commit()


def list_subscriptions(session: Session) -> list[PushSubscription]:
    return list(session.scalars(select(PushSubscription)).all())


def generate_vapid_keys() -> dict[str, str]:
    """Mint a VAPID P-256 pair (public shared with browsers, private stays server-side)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    private = ec.generate_private_key(ec.SECP256R1()).private_numbers().private_value

    def _b64(raw: bytes) -> str:
        return urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    public = ec.derive_private_key(private, ec.SECP256R1()).public_key()
    point = public.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return {"public": _b64(point), "private": _b64(private.to_bytes(32, "big"))}


def vapid_public_key() -> str:
    return os.environ.get("PRE_VAPID_PUBLIC_KEY", "")


def _pywebpush_sender(subscription: PushSubscription, payload: Mapping[str, object]) -> None:
    """Deliver one push via pywebpush. Raises RuntimeError without VAPID keys."""
    from pywebpush import webpush  # type: ignore[import-untyped]

    private = os.environ.get("PRE_VAPID_PRIVATE_KEY", "")
    contact = os.environ.get("PRE_VAPID_CONTACT", "")
    if not private or not contact:
        raise RuntimeError("set PRE_VAPID_PRIVATE_KEY and PRE_VAPID_CONTACT to send push")
    webpush(
        subscription_info={
            "endpoint": subscription.endpoint,
            "keys": {"p256dh": subscription.p256dh, "auth": subscription.auth},
        },
        data=json.dumps(payload),
        vapid_private_key=private,
        vapid_claims={"sub": contact},
    )


def _last_notify(session: Session) -> datetime | None:
    raw = _get_flag(session, LAST_NOTIFY_KEY)
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def notify_new_digest(
    session: Session,
    base_url: str,
    sender: Sender | None = None,
    now: datetime | None = None,
) -> dict[str, int | bool]:
    """Push undecided items once: skips quiet hours, empty inboxes, and repeats.

    Dead endpoints are pruned. Returns a small summary for cron logs.
    """
    from pywebpush import WebPushException

    send = sender or _pywebpush_sender
    moment = _naive(now) if now is not None else _naive(datetime.now(UTC))
    pending = session.scalars(select(DigestItem).where(DigestItem.verdict.is_(None))).all()
    summary: dict[str, int | bool] = {"sent": 0, "pruned": 0, "undecided": len(pending),
                                      "skipped_quiet": False}
    if not pending:
        return summary
    newest = max(_naive(item.assembled_at) for item in pending)
    last = _last_notify(session)
    if last is not None and newest <= _naive(last):
        return summary
    if in_quiet_hours(session, moment):
        summary["skipped_quiet"] = True
        return summary
    subs = list_subscriptions(session)
    if not subs:
        return summary
    top = sorted(pending, key=lambda item: item.score, reverse=True)[:3]
    payload = {
        "title": f"{len(pending)} undecided item{'s' if len(pending) != 1 else ''}",
        "body": "; ".join(item.entity_label for item in top),
        "url": f"{base_url.rstrip('/')}/digest/daily",
    }
    sent, pruned = 0, 0
    for sub in subs:
        try:
            send(sub, payload)
            sent += 1
        except WebPushException as exc:
            status = getattr(getattr(exc, "response", None), "status_code", None)
            if status in (404, 410):
                session.delete(sub)
                pruned += 1
            continue
    session.commit()
    _set_flag(session, LAST_NOTIFY_KEY, moment.isoformat())
    session.commit()
    summary["sent"], summary["pruned"] = sent, pruned
    return summary


__all__ = [
    "LAST_NOTIFY_KEY",
    "QUIET_END_KEY",
    "QUIET_START_KEY",
    "Sender",
    "add_subscription",
    "generate_vapid_keys",
    "get_quiet_hours",
    "in_quiet_hours",
    "list_subscriptions",
    "notify_new_digest",
    "remove_subscription",
    "set_quiet_hours",
    "vapid_public_key",
]
