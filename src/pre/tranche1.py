"""Tranche-1 import orchestration: file -> parser -> confirmation queue, with sync state.

First connect ingests the full available history; re-imports delta (idempotent via
proposal uniqueness, with recurrence strengthening confidence instead of duplicating).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from pre.models import SourceSyncState
from pre.parsers import parse_commerce_csv, parse_financial_csv, parse_takeout_activity
from pre.queue import Proposal, propose

ParserFn = Callable[[str | Path, str | None], list[Proposal]]


def _parse_takeout(path: str | Path, source_ref: str | None = None) -> list[Proposal]:
    return parse_takeout_activity(path)


PARSERS: dict[str, ParserFn] = {
    "financial": parse_financial_csv,
    "commerce": parse_commerce_csv,
    "takeout": _parse_takeout,
}


@dataclass
class ImportResult:
    tier: str
    source_ref: str
    first_connect: bool
    proposals_new: int = 0
    proposals_strengthened: int = 0


def import_source_file(session: Session, tier: str, path: str | Path) -> ImportResult:
    if tier not in PARSERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {sorted(PARSERS)}")
    source_ref = str(path)
    parser = PARSERS[tier]

    state = session.query(SourceSyncState).filter_by(tier=tier, source_ref=source_ref).one_or_none()
    first_connect = state is None
    if state is None:
        state = SourceSyncState(tier=tier, source_ref=source_ref)
        session.add(state)
        session.flush()

    proposals: list[Proposal] = parser(Path(path), source_ref)
    result = ImportResult(tier=tier, source_ref=source_ref, first_connect=first_connect)
    for proposal in proposals:
        before = proposal.confidence
        row = propose(session, proposal)
        if row.observations == 1 and row.status == "pending":
            result.proposals_new += 1
        elif row.confidence > before or row.observations > 1:
            result.proposals_strengthened += 1

    state.records_seen += len(proposals)
    from pre.models import utcnow

    state.last_sync_at = utcnow()
    session.commit()
    return result
