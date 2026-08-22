"""Tranche-2 extraction: comms, productivity/notes, social/content, contacts.

Canonical export shapes (v1 contract, same doctrine as tranche 1):

- comms JSON:     [{"from": "Name <a@b>", "date": "iso"}, ...]
- notes JSON:     {"app": "Obsidian", "pages": [{"title": "...", "updated": "iso"}, ...]}
- social JSON:    {"platform": "LinkedIn",
                   "interactions": [{"type": "post|like|dm", "date": "iso",
                                     "person": "Name"?}, ...]}
- contacts JSON:  [{"name": "...", "organization": "...?", "title": "...?",
                    "dimension": "...?"}, ...]

Comms and social interactions propose People (Network cluster) WITH relationship context
(frequency, recency); the notes app and social platform propose themselves as Tools;
known vendors named in note titles do too. Proposals deduplicate against Tools already
in the Profile.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import SourceSyncState, Tool
from pre.network_extract import enrich_person_proposal
from pre.queue import Proposal, propose

_EMAIL_NAME = re.compile(r"^(.*?)\s*<")
_PLACEHOLDER_NAMES = {"me", "my", "self", "you"}


def _display_name(address: str) -> str:
    address = address.strip()
    match = _EMAIL_NAME.match(address)
    if match and match.group(1).strip():
        name = match.group(1).strip()
    else:
        name = address.split("@")[0].replace(".", " ").replace("_", " ").title()
    return "" if name.lower() in _PLACEHOLDER_NAMES else name


_KNOWN_VENDORS = (
    "notion", "obsidian", "figma", "slack", "github", "linear", "vercel", "openai",
    "anthropic", "apollo", "instantly", "hubspot", "monarch", "postgres",
)


def _iso(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def parse_comms_json(path: str | Path) -> list[Proposal]:
    """Frequent correspondents -> Person proposals for the Network cluster.

    Only the 'from' side counts: the user appears in 'to' of every message.
    Proposals carry relationship context (frequency, recency) for the NetworkLink.
    """
    messages: list[dict[str, Any]] = json.loads(Path(path).read_text(encoding="utf-8"))
    counts: Counter[str] = Counter()
    last_seen: dict[str, str] = {}
    for message in messages:
        sender = message.get("from")
        if not sender:
            continue
        name = _display_name(str(sender))
        if not name:
            continue
        counts[name] += 1
        date = str(message.get("date", "")).strip()
        if date:
            last_seen[name] = max(last_seen.get(name, ""), date)

    return [
        enrich_person_proposal(
            Proposal(
                entity_type="person",
                payload_key=f"person:{name.lower()}",
                payload={"name": name, "messages": occurrences},
                source_tier="comms",
                source_ref=str(path),
                confidence=min(0.95, 0.5 + 0.1 * (occurrences - 1)),
            ),
            occurrences=occurrences,
            last_seen=_iso(last_seen[name]) if name in last_seen else None,
        )
        for name, occurrences in sorted(counts.items())
    ]


def parse_notes_json(path: str | Path) -> list[Proposal]:
    """The notes app itself + known vendors named in page titles -> Tool proposals."""
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    app = str(data.get("app", "")).strip()
    pages: list[dict[str, Any]] = data.get("pages", [])

    proposals: list[Proposal] = []
    if app:
        proposals.append(
            Proposal(
                entity_type="tool",
                payload_key=f"tool:{app.lower()}",
                payload={"name": app, "pages": len(pages)},
                source_tier="notes",
                source_ref=str(path),
                confidence=min(0.95, 0.6 + 0.05 * len(pages)),
            )
        )
    title_text = " ".join(str(p.get("title", "")) for p in pages).lower()
    for vendor in _KNOWN_VENDORS:
        if vendor in title_text:
            proposals.append(
                Proposal(
                    entity_type="tool",
                    payload_key=f"tool:{vendor}",
                    payload={"name": vendor.title(), "evidence": "mentioned in note titles"},
                    source_tier="notes",
                    source_ref=str(path),
                    confidence=0.55,
                )
            )
    return proposals


def parse_social_json(path: str | Path) -> list[Proposal]:
    """Platform -> Tool proposal; interaction partners -> Person proposals."""
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    platform = str(data.get("platform", "")).strip()
    interactions: list[dict[str, Any]] = data.get("interactions", [])

    proposals: list[Proposal] = []
    if platform:
        proposals.append(
            Proposal(
                entity_type="tool",
                payload_key=f"tool:{platform.lower()}",
                payload={"name": platform, "interactions": len(interactions)},
                source_tier="social",
                source_ref=str(path),
                confidence=min(0.95, 0.6 + 0.02 * len(interactions)),
            )
        )
    people: Counter[str] = Counter()
    people_last: dict[str, str] = {}
    for interaction in interactions:
        person = str(interaction.get("person", "")).strip()
        if person:
            people[person] += 1
            date = str(interaction.get("date", "")).strip()
            if date:
                people_last[person] = max(people_last.get(person, ""), date)
    for name, occurrences in sorted(people.items()):
        proposals.append(
            enrich_person_proposal(
                Proposal(
                    entity_type="person",
                    payload_key=f"person:{name.lower()}",
                    payload={"name": name, "interactions": occurrences},
                    source_tier="social",
                    source_ref=str(path),
                    confidence=min(0.9, 0.45 + 0.15 * (occurrences - 1)),
                ),
                occurrences=occurrences,
                last_seen=_iso(people_last[name]) if name in people_last else None,
            )
        )
    return proposals


def parse_contacts_json(path: str | Path) -> list[Proposal]:
    """Contacts export (see network_extract.parse_contacts_json) as a tranche-2 kind."""
    from pre.network_extract import parse_contacts_json as _parse

    return _parse(path)


PARSERS = {
    "comms": parse_comms_json,
    "notes": parse_notes_json,
    "social": parse_social_json,
    "contacts": parse_contacts_json,
}


def _filter_known_tools(session: Session, proposals: list[Proposal]) -> list[Proposal]:
    """Spec criterion: proposals deduplicate against assertions already in the Profile."""
    existing = {name.lower() for name in session.scalars(select(Tool.name)).all()}
    return [p for p in proposals if p.payload_key not in {f"tool:{n}" for n in existing}]


def import_tranche2_file(session: Session, kind: str, path: str | Path) -> dict[str, int]:
    """Full history on first connect, deltas after — same mechanics as tranche 1."""
    if kind not in PARSERS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {sorted(PARSERS)}")
    source_ref = str(path)

    state = (
        session.query(SourceSyncState).filter_by(tier=kind, source_ref=source_ref).one_or_none()
    )
    if state is None:
        state = SourceSyncState(tier=kind, source_ref=source_ref)
        session.add(state)
        session.flush()

    proposals = PARSERS[kind](Path(path))
    before = len(proposals)
    proposals = _filter_known_tools(session, proposals)
    skipped_known = before - len(proposals)

    new_count = 0
    strengthened = 0
    for proposal in proposals:
        confidence_before = proposal.confidence
        row = propose(session, proposal)
        if row.observations == 1 and row.status == "pending":
            new_count += 1
        elif row.confidence > confidence_before or row.observations > 1:
            strengthened += 1

    from pre.models import utcnow

    state.records_seen += before
    state.last_sync_at = utcnow()
    session.commit()
    return {
        "proposals_new": new_count,
        "strengthened": strengthened,
        "skipped_already_in_profile": skipped_known,
    }
