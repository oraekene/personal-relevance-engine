"""Direct applier unit tests (issue 19): writes without lifecycle.

Lifecycle (status, commit, version bump) stays in queue.accept; these tests pin
each applier's row writes and refuse paths in isolation.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.apply import apply_activity, apply_organization, apply_person, apply_tool
from pre.intake import apply_intake_dict
from pre.models import Activity, Need, NetworkLink, Organization, Person, Tool
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


def _need_id(session: Session) -> int:
    apply_intake_dict(
        session,
        {
            "dimensions": [
                {
                    "code": "business",
                    "goals": [{"title": "G", "needs": [{"title": "N"}]}],
                }
            ]
        },
    )
    return session.scalars(select(Need)).one().id


def _activity_proposal(need_id: object) -> Proposal:
    return Proposal(
        entity_type="activity",
        payload_key="activity:standup",
        payload={"title": "Standup", "cadence": "daily", "need_id": need_id},
        source_tier="live-calendar",
        source_ref="cal.json",
    )


def test_apply_activity_creates_under_need(session: Session) -> None:
    prop = propose(session, _activity_proposal(_need_id(session)))

    applied = apply_activity(session, prop)

    assert isinstance(applied, Activity)
    assert applied.title == "Standup"
    assert applied.cadence == "daily"
    assert applied.source == "extraction:live-calendar"
    assert prop.status == "pending"


def test_apply_activity_refuses_without_need(session: Session) -> None:
    need_id = _need_id(session)

    missing_title = propose(
        session,
        Proposal(
            entity_type="activity",
            payload_key="activity:x",
            payload={"need_id": need_id},
            source_tier="live-calendar",
            source_ref="cal.json",
        ),
    )
    assert apply_activity(session, missing_title) is None

    missing_need = propose(
        session,
        Proposal(
            entity_type="activity",
            payload_key="activity:y",
            payload={"title": "Y", "need_id": 9999},
            source_tier="live-calendar",
            source_ref="cal.json",
        ),
    )
    assert apply_activity(session, missing_need) is None
    assert session.query(Activity).count() == 0
