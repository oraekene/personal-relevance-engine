from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.intake import apply_intake_dict, apply_intake_file
from pre.models import (
    Activity,
    Goal,
    LifeDimension,
    Need,
    NetworkLink,
    Person,
    Task,
    Tool,
)
from pre.view import render_profile, render_tool_names

FIXTURE = Path(__file__).parent / "fixtures" / "profile.yaml"


def test_fixture_intake_creates_full_hierarchy(session: Session) -> None:
    summary = apply_intake_file(session, FIXTURE)

    assert summary.dimensions == 2
    assert summary.goals == 3
    assert summary.needs == 3
    assert summary.activities == 4
    assert summary.tasks == 5
    assert summary.tools == 6  # tool-task links (Apollo used by two tasks)
    assert summary.people == 2
    assert summary.organizations == 1
    assert summary.network_links == 3

    dims = session.scalars(select(LifeDimension)).all()
    assert {d.code for d in dims} == {"business", "financial"}
    business = next(d for d in dims if d.code == "business")
    assert business.satisfaction_score == 6
    assert business.source == "interview"
    assert business.confidence == 1.0


def test_every_assertion_is_sourced_interview(session: Session) -> None:
    apply_intake_file(session, FIXTURE)

    for model in (LifeDimension, Goal, Need, Activity, Task, Tool, Person, NetworkLink):
        for row in session.scalars(select(model)).all():
            assert row.source == "interview", f"{model.__name__} row lacks interview source"
            assert row.confidence == 1.0


def test_tools_deduped_across_tasks(session: Session) -> None:
    apply_intake_file(session, FIXTURE)

    tool_names = render_tool_names(session)
    assert sorted(tool_names) == ["Apollo", "HubSpot", "Instantly", "Monarch", "Postgres"]


def test_unknown_dimension_code_rejected_and_rolled_back(session: Session) -> None:
    bad = {
        "dimensions": [
            {"code": "business", "satisfaction": 5},
            {"code": "not_a_dimension"},
        ]
    }
    with pytest.raises(ValueError, match="unknown dimension code"):
        apply_intake_dict(session, bad)

    assert session.scalars(select(LifeDimension)).all() == []


def test_invalid_horizon_rejected(session: Session) -> None:
    bad = {
        "dimensions": [
            {
                "code": "career",
                "goals": [{"title": "X", "horizon": "someday"}],
            }
        ]
    }
    with pytest.raises(ValueError, match="horizon"):
        apply_intake_dict(session, bad)


def test_render_profile_includes_scores_and_network(session: Session) -> None:
    apply_intake_file(session, FIXTURE)

    text = render_profile(session)
    assert "Business" in text
    assert "[satisfaction 6/10]" in text
    assert "Jane Okafor" in text
    assert "Northwind Retail" in text
    assert "Outbound prospecting" in text
    assert "tools: Apollo" in text
