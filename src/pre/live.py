"""Live connectors (ticket 12): calendar + email.

v1 contract: connectors pull from canonical JSON event/message documents — the shape a
real Google Calendar / Gmail API fetcher produces. Swapping in OAuth-backed fetchers
later changes only the `fetch` step; parsing, queueing, auto-accept, and delta logic are
here and tested.

- calendar: recurring/repeated events propose Activities (cadence evidence) — these wait
  for manual acceptance because they need a parent Need in the hierarchy.
- email: frequent correspondents propose People (Network); subjects naming known vendors
  propose Tools — high-confidence tool proposals are the pre-approved auto-accept class.

Every import is full-history-first, then idempotent deltas (queue uniqueness).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from pre.models import SourceSyncState
from pre.queue import Proposal, propose, run_auto_accept

_KNOWN_VENDORS = (
    "notion", "obsidian", "figma", "slack", "github", "linear", "vercel", "openai",
    "anthropic", "apollo", "instantly", "hubspot", "monarch",
)

_CADENCE_HINTS = (
    ("weekly", re.compile(r"\bweekly\b", re.IGNORECASE)),
    ("daily", re.compile(r"\bdaily|standup|stand-up\b", re.IGNORECASE)),
    ("monthly", re.compile(r"\bmonthly|1:1|one-on-one\b", re.IGNORECASE)),
)


def _display_name(address: str) -> str:
    address = address.strip()
    match = re.match(r"^(.*?)\s*<", address)
    if match and match.group(1).strip():
        return match.group(1).strip()
    return address.split("@")[0].replace(".", " ").replace("_", " ").title()


def parse_calendar_events(path: str | Path) -> list[Proposal]:
    """Repeated event titles -> Activity proposals with cadence evidence."""
    events: list[dict[str, Any]] = json.loads(Path(path).read_text(encoding="utf-8"))
    titles: Counter[str] = Counter()
    for event in events:
        title = str(event.get("title", "")).strip()
        if title:
            titles[title] += 1

    proposals: list[Proposal] = []
    for title, occurrences in sorted(titles.items()):
        cadence = next(
            (name for name, pattern in _CADENCE_HINTS if pattern.search(title)), None
        )
        if occurrences < 2 and cadence is None:
            continue  # one-off events are not Activities
        proposals.append(
            Proposal(
                entity_type="activity",
                payload_key=f"activity:{title.lower()}",
                payload={"title": title, "cadence": cadence, "events_seen": occurrences},
                source_tier="live-calendar",
                source_ref=str(path),
                confidence=min(0.9, 0.5 + 0.1 * (occurrences - 1)),
            )
        )
    return proposals


def parse_email_messages(path: str | Path) -> list[Proposal]:
    """Frequent senders -> People; vendor-named subjects -> Tools."""
    messages: list[dict[str, Any]] = json.loads(Path(path).read_text(encoding="utf-8"))
    senders: Counter[str] = Counter()
    subject_text = ""
    for message in messages:
        sender = _display_name(str(message.get("from", "")))
        if sender:
            senders[sender] += 1
        subject_text += f" {message.get('subject', '')!s}".lower()

    proposals: list[Proposal] = []
    for name, count in senders.items():
        if count >= 2:
            proposals.append(
                Proposal(
                    entity_type="person",
                    payload_key=f"person:{name.lower()}",
                    payload={"name": name, "messages": count},
                    source_tier="live-email",
                    source_ref=str(path),
                    confidence=min(0.95, 0.55 + 0.1 * (count - 2)),
                )
            )
    lowered = subject_text
    for vendor in _KNOWN_VENDORS:
        if vendor in lowered:
            proposals.append(
                Proposal(
                    entity_type="tool",
                    payload_key=f"tool:{vendor}",
                    payload={"name": vendor.title(), "evidence": "named in email subjects"},
                    source_tier="live-email",
                    source_ref=str(path),
                    confidence=0.9,  # eligible for the auto-accept class
                )
            )
    return proposals


KINDS = {"calendar": parse_calendar_events, "email": parse_email_messages}


def import_live_file(session: Session, kind: str, path: str | Path) -> dict[str, int]:
    """Pull one live-source document into the queue, then run the auto-accept rule."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {sorted(KINDS)}")
    tier = f"live-{kind}"
    source_ref = str(path)

    state = (
        session.query(SourceSyncState).filter_by(tier=tier, source_ref=source_ref).one_or_none()
    )
    if state is None:
        state = SourceSyncState(tier=tier, source_ref=source_ref)
        session.add(state)
        session.flush()

    proposals = KINDS[kind](Path(path))
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

    state.records_seen += len(proposals)
    state.last_sync_at = utcnow()
    session.commit()

    auto_accepted = run_auto_accept(session)
    return {
        "proposals_new": new_count,
        "strengthened": strengthened,
        "auto_accepted": auto_accepted,
    }
