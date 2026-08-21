from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import Person, SourceSyncState, Tool
from pre.queue import list_pending
from pre.tranche2 import import_tranche2_file, parse_comms_json, parse_notes_json, parse_social_json

FIXTURES = Path(__file__).parent / "fixtures"


def test_comms_parser_extracts_correspondents(tmp_path: Path) -> None:
    doc = tmp_path / "comms.json"
    doc.write_text(
        '[{"from": "Maya Chen <maya@x.co>", "to": ["me"], "date": "2026-08-01"},'
        ' {"from": "Maya Chen <maya@x.co>", "to": ["me"], "date": "2026-08-02"},'
        ' {"from": "sam.reyes@x.co", "to": ["me"], "date": "2026-08-03"}]',
        encoding="utf-8",
    )
    proposals = {p.payload["name"]: p for p in parse_comms_json(doc)}

    assert set(proposals) == {"Maya Chen", "Sam Reyes"}
    assert proposals["Maya Chen"].confidence > proposals["Sam Reyes"].confidence
    assert all(p.entity_type == "person" for p in proposals.values())


def test_notes_parser_proposes_app_and_mentioned_vendors() -> None:
    proposals = {p.payload["name"]: p for p in parse_notes_json(FIXTURES / "notes.json")}

    assert "Obsidian" in proposals
    assert "Notion" in proposals  # named in a page title
    assert all(p.entity_type == "tool" for p in proposals.values())


def test_social_parser_proposes_platform_and_people() -> None:
    proposals = parse_social_json(FIXTURES / "social.json")

    by_type = {(p.payload["name"], p.entity_type) for p in proposals}
    assert ("LinkedIn", "tool") in by_type
    assert ("Maya Chen", "person") in by_type


def test_tranche2_import_routes_through_queue(session: Session) -> None:
    result = import_tranche2_file(session, "notes", FIXTURES / "notes.json")

    assert result["proposals_new"] >= 2
    assert session.query(Tool).count() == 0  # queue never writes the Profile directly
    assert len(list_pending(session)) == result["proposals_new"]


def test_tranche2_dedupes_against_tools_already_in_profile(session: Session) -> None:
    session.add(Tool(name="Obsidian"))
    session.commit()

    result = import_tranche2_file(session, "notes", FIXTURES / "notes.json")

    assert result["skipped_already_in_profile"] == 1
    pending_names = {
        p.payload_json.get("name")
        for p in list_pending(session)
        if p.entity_type == "tool"
    }
    assert "Obsidian" not in pending_names


def test_tranche2_full_history_then_delta(session: Session) -> None:
    first = import_tranche2_file(session, "social", FIXTURES / "social.json")
    second = import_tranche2_file(session, "social", FIXTURES / "social.json")

    assert first["proposals_new"] >= 2
    assert second["proposals_new"] == 0
    state = session.scalars(select(SourceSyncState)).one()
    assert state.tier == "social"
    # records_seen accumulates across runs (2 proposals per run on this fixture):
    assert state.records_seen == 2 * 2


def test_unknown_kind_rejected(session: Session) -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        import_tranche2_file(session, "browser-history", FIXTURES / "notes.json")


def test_person_proposals_are_network_cluster(session: Session) -> None:
    import_tranche2_file(session, "comms", FIXTURES / "email.json")

    people_proposals = [
        p for p in list_pending(session) if p.entity_type == "person"
    ]
    assert people_proposals, "email fixture should propose at least one person"
    # Nothing written to the Network cluster before acceptance:
    assert session.query(Person).count() == 0
