"""Network extraction (ticket 10): People/Organizations WITH relationship context.

Builds on tranche-2 parsers: person proposals now carry frequency, recency, role, and
Life Dimension link, so accepting a person also writes their NetworkLink. Adds the
contacts source (canonical JSON: [{"name": ..., "organization": ...?, "title": ...?,
"dimension": ...?}]).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pre.queue import Proposal

_FREQUENCY_THRESHOLDS = ((12, "weekly"), (3, "monthly"))
_RECENCY_BUCKETS = ((7, "this-week"), (31, "this-month"), (90, "this-quarter"))


def recency_bucket(last_seen: datetime | None, now: datetime | None = None) -> str:
    if last_seen is None:
        return "unknown"
    now = now or datetime.now(UTC)
    if last_seen.tzinfo is None:
        last_seen = last_seen.replace(tzinfo=UTC)  # date-only exports are UTC by contract
    days = max(0, (now - last_seen).days)
    for limit, label in _RECENCY_BUCKETS:
        if days <= limit:
            return label
    return "stale"


def frequency_bucket(occurrences: int) -> str:
    for threshold, label in _FREQUENCY_THRESHOLDS:
        if occurrences >= threshold:
            return label
    return "adhoc"


def enrich_person_proposal(
    proposal: Proposal,
    occurrences: int,
    last_seen: datetime | None,
    role: str | None = None,
    dimension_code: str | None = None,
    now: datetime | None = None,
) -> Proposal:
    """Attach NetworkLink fields to a person proposal's payload."""
    payload: dict[str, Any] = {**proposal.payload}
    payload["frequency"] = frequency_bucket(occurrences)
    payload["recency"] = recency_bucket(last_seen, now)
    if role:
        payload["role"] = role
    if dimension_code:
        payload["dimension_code"] = dimension_code
    return Proposal(
        entity_type=proposal.entity_type,
        payload_key=proposal.payload_key,
        payload=payload,
        source_tier=proposal.source_tier,
        source_ref=proposal.source_ref,
        confidence=proposal.confidence,
    )


def parse_contacts_json(path: str | Path) -> list[Proposal]:
    """Contacts export -> Organization + Person proposals with roles."""
    contacts: list[dict[str, Any]] = json.loads(Path(path).read_text(encoding="utf-8"))

    orgs: dict[str, None] = {}
    people: list[Proposal] = []
    for contact in contacts:
        name = str(contact.get("name", "")).strip()
        if not name:
            continue
        org_name = str(contact.get("organization", "")).strip() or None
        title = str(contact.get("title", "")).strip() or None
        dimension = str(contact.get("dimension", "")).strip() or None
        if org_name:
            orgs.setdefault(org_name, None)
        people.append(
            enrich_person_proposal(
                Proposal(
                    entity_type="person",
                    payload_key=f"person:{name.lower()}",
                    payload={"name": name},
                    source_tier="contacts",
                    source_ref=str(path),
                    confidence=0.7,
                ),
                occurrences=1,
                last_seen=None,
                role=title or ("member of " + org_name if org_name else None),
                dimension_code=dimension,
            )
        )

    org_proposals = [
        Proposal(
            entity_type="organization",
            payload_key=f"organization:{org.lower()}",
            payload={"name": org},
            source_tier="contacts",
            source_ref=str(path),
            confidence=0.7,
        )
        for org in sorted(orgs)
    ]
    return [*org_proposals, *people]
