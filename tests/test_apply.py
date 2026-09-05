"""Direct applier unit tests (issue 19): writes without lifecycle.

Lifecycle (status, commit, version bump) stays in queue.accept; these tests pin
each applier's row writes and refuse paths in isolation.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from pre.apply import apply_tool
from pre.models import Tool
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
