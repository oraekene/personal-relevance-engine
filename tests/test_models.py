from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pre.models import (
    Activity,
    Goal,
    LifeDimension,
    Need,
    NetworkLink,
    Organization,
    Person,
    Task,
    TaskTool,
    Tool,
    provenance_of,
)


def test_dimension_provenance_defaults(session: Session) -> None:
    dim = LifeDimension(code="career", name="Career")
    session.add(dim)
    session.flush()

    assert dim.source == "interview"
    assert dim.confidence == 1.0
    assert isinstance(dim.last_confirmed_at, datetime)


def test_update_satisfaction_sets_confirmation_time(session: Session) -> None:
    dim = LifeDimension(code="financial", name="Financial")
    session.add(dim)
    session.flush()

    before = dim.last_confirmed_at
    dim.update_satisfaction(7)
    session.flush()

    assert dim.satisfaction_score == 7
    assert dim.last_confirmed_at >= before


def test_update_satisfaction_rejects_out_of_range(session: Session) -> None:
    dim = LifeDimension(code="social", name="Social")
    session.add(dim)
    with pytest.raises(ValueError, match="0-10"):
        dim.update_satisfaction(11)


def test_full_hierarchy_roundtrip(session: Session) -> None:
    dim = LifeDimension(code="business", name="Business")
    goal = Goal(dimension=dim, title="Grow to 20k MRR", horizon="longterm")
    need = Need(goal=goal, title="Lead flow", horizon="immediate", pain_level=7,
                openness_to_change="high")
    activity = Activity(need=need, title="Outbound prospecting", cadence="daily")
    task = Task(activity=activity, title="Pull new leads")
    tool = Tool(name="Apollo")
    session.add(TaskTool(task=task, tool=tool))
    session.commit()

    loaded = session.query(LifeDimension).one()
    assert loaded.goals[0].needs[0].activities[0].tasks[0].task_tools[0].tool.name == "Apollo"
    assert provenance_of(loaded)["source"] == "interview"


def test_tool_names_deduplicated(session: Session) -> None:
    t1 = Tool(name="Apollo")
    session.add(t1)
    session.flush()
    with pytest.raises(IntegrityError):
        session.add(Tool(name="Apollo"))
        session.flush()


def test_network_link_requires_exactly_one_target(session: Session) -> None:
    person = Person(display_name="Jane")
    org = Organization(name="Acme")
    session.add_all([person, org])
    session.flush()

    ok = NetworkLink(person_id=person.id, role="mentor")
    session.add(ok)
    session.flush()

    both = NetworkLink(person_id=person.id, organization_id=org.id)
    session.add(both)
    with pytest.raises(IntegrityError):
        session.flush()


def test_network_link_rejects_empty_target(session: Session) -> None:
    session.add(NetworkLink(role="orphan"))
    with pytest.raises(IntegrityError):
        session.flush()
