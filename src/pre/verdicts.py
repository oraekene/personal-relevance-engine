"""Profile versioning + Verdict capture (ticket 06).

The Profile version is a monotonically increasing counter (SystemFlag) bumped every
time an assertion is written into the Profile — intake application and queue acceptance.
Digest items record the version in force at assembly; Verdicts log against it, so
calibration can always be interpreted against the Profile that produced the item.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import DigestItem, SystemFlag, VerdictLog

VERSION_KEY = "profile_version"
VALID_VERDICTS = ("act", "dismiss")


def get_profile_version(session: Session) -> int:
    flag = session.scalar(select(SystemFlag).where(SystemFlag.key == VERSION_KEY))
    return int(flag.value) if flag else 0


def bump_profile_version(session: Session) -> int:
    """Increment and persist the Profile version; returns the new value."""
    current = get_profile_version(session)
    next_version = current + 1
    flag = session.scalar(select(SystemFlag).where(SystemFlag.key == VERSION_KEY))
    if flag is None:
        session.add(SystemFlag(key=VERSION_KEY, value=str(next_version)))
    else:
        flag.value = str(next_version)
    session.commit()
    return next_version


def record_verdict(
    session: Session, digest_item_id: int, verdict: str, channel: str = "cli"
) -> VerdictLog:
    """One-tap Verdict on a Digest item. Exactly one per item; audit-logged."""
    if verdict not in VALID_VERDICTS:
        raise ValueError(f"verdict must be one of {VALID_VERDICTS}, got {verdict!r}")
    item = session.get(DigestItem, digest_item_id)
    if item is None:
        raise ValueError(f"digest item {digest_item_id} not found")
    if item.verdict is not None:
        raise ValueError(f"item {digest_item_id} already has a verdict ({item.verdict})")

    item.verdict = verdict
    item.verdict_at = datetime.now(UTC)
    log_row = VerdictLog(
        digest_item_id=item.id,
        change_id=item.change_id,
        digest_kind=item.digest_kind,
        dimension_code=item.dimension_code,
        verdict=verdict,
        profile_version=item.profile_version or 0,
        channel=channel,
    )
    session.add(log_row)
    session.commit()
    return log_row


def render_verdict_summary(session: Session) -> str:
    from collections import Counter

    logs = session.scalars(select(VerdictLog)).all()
    counts: Counter[str] = Counter(log.verdict for log in logs)
    total = len(logs)
    lines = [
        (
            f"VERDICTS: {total} recorded "
            f"(act {counts.get('act', 0)}, dismiss {counts.get('dismiss', 0)})"
        )
    ]
    for log in reversed(logs[-10:]):
        lines.append(f"  #{log.digest_item_id} {log.verdict:<8} pv{log.profile_version} "
                     f"via {log.channel} at {log.recorded_at:%Y-%m-%d %H:%M}")
    return "\n".join(lines)
