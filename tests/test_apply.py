"""Direct applier unit tests (issue 19): writes without lifecycle.

Lifecycle (status, commit, version bump) stays in queue.accept; these tests pin
each applier's row writes and refuse paths in isolation.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from pre.apply import apply_organization, apply_person, apply_tool
from pre.models import NetworkLink, Organization, Person, Tool
from pre.queue import Proposal, propose


def _tool_proposal(name: str = "Apollo") -> Proposal:
    return Proposal(
        entity_type="tool",
        payload_key="tool:apollo",
        payload={"name": name},
        source_tier="commerce",
        source_ref="o.csv",
        confidence=0.8,
    )


def test_apply_tool_writes_provenance_without_lifecycle(session: Session) -> None:
    prop = propose(session, _tool_proposal())

    applied = apply_tool(session, prop)

    assert isinstance(applied, Tool)
    assert applied.name == "Apollo"
    assert applied.source == "extraction:commerce"
    assert applied.confidence == 0.8
    assert prop.status == "pending"  # lifecycle stays in queue.accept


def test_apply_tool_refuses_blank_name(session: Session) -> None:
    prop = propose(session, _tool_proposal(name="  "))

    assert apply_tool(session, prop) is None
    assert session.query(Tool).count() == 0


def _person_proposal() -> Proposal:
    return Proposal(
        entity_type="person",
        payload_key="person:maya chen",
        payload={
            "name": "Maya Chen",
            "frequency": "weekly",
            "recency": "this-week",
            "role": "mentor",
            "dimension_code": "career",
        },
        source_tier="comms",
        source_ref="c.json",
        confidence=0.8,
    )


def test_apply_person_writes_person_and_link(session: Session) -> None:
    from sqlalchemy import select

    prop = propose(session, _person_proposal())

    applied = apply_person(session, prop)

    assert isinstance(applied, Person)
    assert applied.source == "extraction:comms"
    link = session.scalars(select(NetworkLink)).one()
    assert link.person_id == applied.id
    assert link.frequency == "weekly"
    assert link.role == "mentor"
    assert link.dimension_code == "career"
    assert prop.status == "pending"


def test_apply_person_refuses_blank_name(session: Session) -> None:
    prop = propose(
        session,
        Proposal(
            entity_type="person",
            payload_key="person:x",
            payload={"name": "  "},
            source_tier="comms",
            source_ref="c.json",
        ),
    )

    assert apply_person(session, prop) is None
    assert session.query(Person).count() == 0


def test_apply_organization_writes_org_and_link(session: Session) -> None:
    from sqlalchemy import select

    prop = propose(
        session,
        Proposal(
            entity_type="organization",
            payload_key="organization:quinn labs",
            payload={"name": "Quinn Labs"},
            source_tier="contacts",
            source_ref="c.json",
            confidence=0.7,
        ),
    )

    applied = apply_organization(session, prop)

    assert isinstance(applied, Organization)
    assert applied.name == "Quinn Labs"
    link = session.scalars(select(NetworkLink)).one()
    assert link.organization_id == applied.id
    assert link.person_id is None
    assert link.source == "extraction:contacts"
