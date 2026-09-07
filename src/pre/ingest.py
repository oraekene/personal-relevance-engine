"""Single ingestion adapter: every source kind imports through one pipeline (issue 21, C2).

Per-kind variance is declarative: the registry maps each kind to an importer
carrying its parser and tier. Only genuinely behavioral variance gets a
subclass (LiveImporter in commit 3: tier prefix + auto-accept hook).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.live import parse_calendar_events, parse_email_messages
from pre.models import SourceSyncState, Tool
from pre.parsers import parse_commerce_csv, parse_financial_csv, parse_takeout_activity
from pre.queue import Proposal, propose, run_auto_accept
from pre.tranche2 import (
    parse_comms_json,
    parse_contacts_json,
    parse_notes_json,
    parse_social_json,
)
from pre.tranche3 import parse_device_history, parse_health_export, parse_work_systems

ParserFn = Callable[[Path], list[Proposal]]


@dataclass
class ImportResult:
    tier: str
    source_ref: str
    first_connect: bool
    proposals_new: int = 0
    proposals_strengthened: int = 0
    skipped_known: int = 0
    auto_accepted: int = 0


class BaseImporter:
    """One import pipeline: sync-state, parse, known-tools filter, propose, count."""

    def __init__(self, kind: str, parser: ParserFn, tier: str | None = None) -> None:
        self.kind = kind
        self.parser = parser
        self.tier = tier or kind

    def post_import(self, session: Session) -> int:
        """Hook: follow-up work after commit (live auto-accept overrides)."""
        return 0

    def import_file(self, session: Session, path: str | Path) -> ImportResult:
        source_ref = str(path)
        state = (
            session.query(SourceSyncState)
            .filter_by(tier=self.tier, source_ref=source_ref)
            .one_or_none()
        )
        first_connect = state is None
        if state is None:
            state = SourceSyncState(tier=self.tier, source_ref=source_ref)
            session.add(state)
            session.flush()

        parsed = self.parser(Path(path))
        kept = _filter_known_tools(session, parsed)
        result = ImportResult(
            tier=self.tier,
            source_ref=source_ref,
            first_connect=first_connect,
            skipped_known=len(parsed) - len(kept),
        )
        for proposal in kept:
            before = proposal.confidence
            row = propose(session, proposal)
            if row.observations == 1 and row.status == "pending":
                result.proposals_new += 1
            elif row.confidence > before or row.observations > 1:
                result.proposals_strengthened += 1

        from pre.models import utcnow

        state.records_seen += len(kept)
        state.last_sync_at = utcnow()
        session.commit()
        result.auto_accepted = self.post_import(session)
        return result


class LiveImporter(BaseImporter):
    """Live connectors: same pipeline plus the auto-accept rule (ticket 12)."""

    def __init__(self, kind: str, parser: ParserFn) -> None:
        super().__init__(kind, parser, tier=f"live-{kind}")

    def post_import(self, session: Session) -> int:
        return run_auto_accept(session)


def _filter_known_tools(session: Session, proposals: list[Proposal]) -> list[Proposal]:
    """Spec criterion (ticket 09): proposals deduplicate against owned Tools."""
    existing = {name.lower() for name in session.scalars(select(Tool.name)).all()}
    return [p for p in proposals if p.payload_key not in {f"tool:{n}" for n in existing}]


IMPORTERS: dict[str, BaseImporter] = {
    "financial": BaseImporter("financial", parse_financial_csv),
    "commerce": BaseImporter("commerce", parse_commerce_csv),
    "takeout": BaseImporter("takeout", parse_takeout_activity),
    "comms": BaseImporter("comms", parse_comms_json),
    "notes": BaseImporter("notes", parse_notes_json),
    "social": BaseImporter("social", parse_social_json),
    "contacts": BaseImporter("contacts", parse_contacts_json),
    "device": BaseImporter("device", parse_device_history),
    "health": BaseImporter("health", parse_health_export),
    "work-systems": BaseImporter("work-systems", parse_work_systems),
    "calendar": LiveImporter("calendar", parse_calendar_events),
    "email": LiveImporter("email", parse_email_messages),
}


def import_file(session: Session, kind: str, path: str | Path) -> ImportResult:
    """One entry point for every source kind; unknown kinds fail loud."""
    try:
        importer = IMPORTERS[kind]
    except KeyError:
        raise ValueError(f"unknown kind {kind!r}; expected one of {sorted(IMPORTERS)}") from None
    return importer.import_file(session, path)


__all__ = ["IMPORTERS", "BaseImporter", "ImportResult", "ParserFn", "import_file"]
