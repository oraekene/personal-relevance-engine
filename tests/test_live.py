from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.intake import apply_intake_dict
from pre.live import import_live_file, parse_calendar_events, parse_email_messages
from pre.models import Activity, Person, ProposedAssertion, Tool
from pre.queue import list_pending
from pre.watchlist import active_watchlist_product_names, sync_watchlist

FIXTURES = Path(__file__).parent / "fixtures"


# --- calendar: recurring events -> Activity proposals --------------------------


def test_calendar_parser_extracts_recurring_titles() -> None:
    proposals = {p.payload["title"]: p for p in parse_calendar_events(FIXTURES / "calendar.json")}

    assert set(proposals) == {"Daily standup", "Weekly pipeline review"}
    assert "Dentist" not in proposals  # one-off events are not Activities
    assert proposals["Daily standup"].payload["cadence"] == "daily"
    assert proposals["Weekly pipeline review"].payload["cadence"] == "weekly"


def test_calendar_proposals_wait_for_manual_acceptance(session: Session) -> None:
    result = import_live_file(session, "calendar", FIXTURES / "calendar.json")

    assert result["proposals_new"] == 2
    assert result["auto_accepted"] == 0  # activities are structural -> human decides
    pending_types = {p.entity_type for p in list_pending(session)}
    assert pending_types == {"activity"}


def test_activity_acceptance_requires_parent_need(session: Session) -> None:
    from pre.queue import accept

    import_live_file(session, "calendar", FIXTURES / "calendar.json")
    proposal = next(p for p in list_pending(session))

    # Without need_id the acceptance is refused:
    assert accept(session, proposal.id) is None
    assert proposal.status == "pending"

    # With a real Need it lands in the hierarchy with provenance:
    apply_intake_dict(
        session,
        {
            "dimensions": [
                {
                    "code": "business",
                    "goals": [
                        {
                            "title": "Run the agency",
                            "needs": [{"title": "Keep the machine running"}],
                        }
                    ],
                }
            ]
        },
    )
    from pre.models import Need

    need = session.query(Need).one()
    proposal.payload_json = {**proposal.payload_json, "need_id": need.id}
    session.commit()

    applied = accept(session, proposal.id)
    assert isinstance(applied, Activity)
    assert applied.need_id == need.id
    assert applied.source == "extraction:live-calendar"
    assert applied.cadence == "daily"


# --- email: people + auto-accepted tools ---------------------------------------


def test_email_parser_frequent_senders_and_vendor_subjects() -> None:
    proposals = {(p.entity_type, p.payload["name"]): p for p in parse_email_messages(
        FIXTURES / "email.json"
    )}

    assert ("person", "Maya Chen") in proposals  # 2 messages
    assert ("person", "Notifications") not in proposals  # 1 message, below threshold
    assert ("tool", "Github") in proposals
    assert ("tool", "Linear") in proposals


def test_auto_accept_class_fires_with_audit_trail(session: Session) -> None:
    result = import_live_file(session, "email", FIXTURES / "email.json")

    assert result["auto_accepted"] >= 2  # Github + Linear at confidence 0.9
    accepted = (
        session.scalars(
            select(ProposedAssertion).where(ProposedAssertion.status == "accepted")
        ).all()
    )
    assert {p.decided_via for p in accepted} == {"auto-rule"}
    assert all(p.decided_at is not None for p in accepted)
    tool_names = {t.name for t in session.query(Tool).all()}
    assert {"Github", "Linear"} <= tool_names


def test_accepted_live_tools_join_watchlist(session: Session) -> None:
    import_live_file(session, "email", FIXTURES / "email.json")
    sync_watchlist(session)

    names = active_watchlist_product_names(session)
    assert {"Github", "Linear"} <= set(names)


def test_people_from_email_stay_pending_for_human_decision(session: Session) -> None:
    import_live_file(session, "email", FIXTURES / "email.json")

    person_props = [p for p in list_pending(session) if p.entity_type == "person"]
    assert len(person_props) == 1
    assert session.query(Person).count() == 0


def test_live_import_is_idempotent_delta(session: Session) -> None:
    first = import_live_file(session, "email", FIXTURES / "email.json")
    second = import_live_file(session, "email", FIXTURES / "email.json")

    assert second["proposals_new"] == 0
    assert second["auto_accepted"] == 0  # already decided on first pull
    # Total rows never grows: accepted + pending == what the first pull proposed.
    total = session.query(ProposedAssertion).count()
    assert total == first["proposals_new"]
    pending_after = len(list_pending(session))
    assert pending_after == first["proposals_new"] - first["auto_accepted"]


def test_unknown_kind_rejected(session: Session) -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        import_live_file(session, "sms", FIXTURES / "email.json")
