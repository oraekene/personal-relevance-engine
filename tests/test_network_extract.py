from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.intake import apply_intake_dict
from pre.models import NetworkLink, Person
from pre.network_extract import (
    enrich_person_proposal,
    frequency_bucket,
    parse_contacts_json,
    recency_bucket,
)
from pre.queue import Proposal, accept, list_pending, propose
from pre.retrieval import index_all
from pre.tranche2 import import_tranche2_file

FIXTURES = Path(__file__).parent / "fixtures"


# --- buckets --------------------------------------------------------------------


def test_frequency_bucket_thresholds() -> None:
    assert frequency_bucket(1) == "adhoc"
    assert frequency_bucket(3) == "monthly"
    assert frequency_bucket(12) == "weekly"


def test_recency_bucket_handles_naive_and_aware() -> None:
    now = datetime(2026, 8, 20, tzinfo=UTC)
    assert recency_bucket(datetime(2026, 8, 18), now=now) == "this-week"  # noqa: DTZ001
    assert recency_bucket(datetime(2026, 8, 10), now=now) == "this-month"  # noqa: DTZ001
    assert recency_bucket(datetime(2026, 7, 1), now=now) == "this-quarter"  # noqa: DTZ001
    assert recency_bucket(datetime(2026, 1, 1), now=now) == "stale"  # noqa: DTZ001
    assert recency_bucket(None) == "unknown"


def test_enrich_attaches_relationship_fields() -> None:
    base = Proposal(
        entity_type="person",
        payload_key="person:x",
        payload={"name": "X"},
        source_tier="comms",
        source_ref="f.json",
    )
    enriched = enrich_person_proposal(
        base,
        occurrences=12,
        last_seen=datetime(2026, 8, 19),  # noqa: DTZ001 -- naive input is the contract
        role="mentor",
        dimension_code="career",
    )

    assert enriched.payload["frequency"] == "weekly"
    assert enriched.payload["recency"] in ("this-week", "this-month")
    assert enriched.payload["role"] == "mentor"
    assert enriched.payload["dimension_code"] == "career"


# --- contacts source --------------------------------------------------------------


def test_contacts_parser_proposes_orgs_people_with_roles(tmp_path: Path) -> None:
    doc = tmp_path / "contacts.json"
    doc.write_text(
        '[{"name": "Maya Chen", "organization": "Chen Consulting", '
        '"title": "Mentor", "dimension": "career"},'
        ' {"name": "Sam Reyes"}]',
        encoding="utf-8",
    )
    proposals = {p.payload["name"]: p for p in parse_contacts_json(doc)}

    org = next(p for p in proposals.values() if p.entity_type == "organization")
    assert org.payload["name"] == "Chen Consulting"
    maya = proposals["Maya Chen"]
    assert maya.payload["role"] == "Mentor"
    assert maya.payload["dimension_code"] == "career"


def test_contacts_kind_imports_through_queue(session: Session, tmp_path: Path) -> None:
    doc = tmp_path / "contacts.json"
    doc.write_text('[{"name": "Ada Quinn", "organization": "Quinn Labs"}]', encoding="utf-8")

    result = import_tranche2_file(session, "contacts", doc)

    assert result["proposals_new"] == 2  # one organization + one person
    types = {p.entity_type for p in list_pending(session)}
    assert types == {"organization", "person"}


# --- accepting a person writes the NetworkLink -------------------------------------


def test_accepting_person_with_context_writes_network_link(session: Session) -> None:
    prop = propose(
        session,
        Proposal(
            entity_type="person",
            payload_key="person:maya chen",
            payload={
                "name": "Maya Chen",
                "messages": 14,
                "frequency": "weekly",
                "recency": "this-week",
                "role": "mentor",
                "dimension_code": "career",
            },
            source_tier="comms",
            source_ref="comms.json",
            confidence=0.8,
        ),
    )
    person = accept(session, prop.id)

    assert isinstance(person, Person)
    link = session.scalars(select(NetworkLink)).one()
    assert link.person_id == person.id
    assert link.frequency == "weekly"
    assert link.recency == "this-week"
    assert link.role == "mentor"
    assert link.dimension_code == "career"
    assert link.source == "extraction:comms"


def test_link_created_once_per_tier_even_on_repeat_acceptance(session: Session) -> None:

    # comms tier proposes Maya; accept; then social tier proposes her again; accept again.
    first = propose(
        session,
        Proposal(
            entity_type="person",
            payload_key="person:maya",
            payload={"name": "Maya Chen", "frequency": "weekly", "recency": "this-week"},
            source_tier="comms",
            source_ref="c.json",
        ),
    )
    accept(session, first.id)

    second = propose(
        session,
        Proposal(
            entity_type="person",
            payload_key="person:maya",
            payload={"name": "Maya Chen", "frequency": "monthly"},
            source_tier="social",
            source_ref="s.json",
        ),
    )
    accept(session, second.id)

    links = session.scalars(select(NetworkLink)).all()
    assert len(links) == 2  # one per evidence tier, not duplicated within a tier
    assert session.query(Person).count() == 1


def test_network_entities_embeddable_after_index(session: Session) -> None:
    from pre.change_corpus import FirehoseEntry, ingest_entries
    from pre.models import Change
    from pre.retrieval import shortlist_for_change

    apply_intake_dict(
        session,
        {
            "dimensions": [
                {
                    "code": "career",
                    "goals": [
                        {
                            "title": "Learn from mentors",
                            "needs": [{"title": "Regular mentor conversations"}],
                        }
                    ],
                }
            ]
        },
    )
    prop = propose(
        session,
        Proposal(
            entity_type="person",
            payload_key="person:jane",
            payload={"name": "Jane Okafor", "frequency": "monthly", "recency": "this-month"},
            source_tier="contacts",
            source_ref="contacts.json",
        ),
    )
    accept(session, prop.id)

    entry = FirehoseEntry(product_name="People", title="Jane Okafor monthly mentor chat")
    ingest_entries(session, [entry], "lane")
    index_all(session)

    change = session.scalars(select(Change)).one()
    candidates = shortlist_for_change(session, change.id, top_k=5)
    assert any(c.entity_type == "person" for c in candidates), (
        "accepted Network people should be retrievable against Changes"
    )
