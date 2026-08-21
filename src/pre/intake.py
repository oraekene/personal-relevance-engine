"""Intake: build the Profile skeleton from a structured interview.

Two modes:
- batch: apply a YAML file (tests, agent-assisted interviews)
- interactive: walk every Life Dimension at the terminal

Everything written here is an assertion with source="interview", confidence=1.0.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import select
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
)
from pre.taxonomy import CADENCES, DIMENSIONS_BY_CODE, HORIZONS, OPENNESS


@dataclass
class IntakeSummary:
    dimensions: int = 0
    goals: int = 0
    needs: int = 0
    activities: int = 0
    tasks: int = 0
    tools: int = 0
    people: int = 0
    organizations: int = 0
    network_links: int = 0
    errors: list[str] = field(default_factory=list)

    def total(self) -> int:
        return (
            self.dimensions
            + self.goals
            + self.needs
            + self.activities
            + self.tasks
            + self.tools
            + self.people
            + self.organizations
            + self.network_links
        )


def _check_choice(value: str | None, allowed: tuple[str, ...], label: str) -> str | None:
    if value is None:
        return None
    if value not in allowed:
        raise ValueError(f"{label} must be one of {allowed}, got {value!r}")
    return value


def _get_or_create_dimension(session: Session, code: str) -> LifeDimension:
    dim = session.scalar(select(LifeDimension).where(LifeDimension.code == code))
    if dim is None:
        meta = DIMENSIONS_BY_CODE.get(code)
        if meta is None:
            raise ValueError(f"unknown dimension code {code!r} (not in canonical taxonomy)")
        dim = LifeDimension(code=meta.code, name=meta.name, description=meta.description)
        session.add(dim)
        session.flush()
    return dim


def _get_or_create_tool(session: Session, name: str) -> Tool:
    tool = session.scalar(select(Tool).where(Tool.name == name))
    if tool is None:
        tool = Tool(name=name)
        session.add(tool)
        session.flush()
    return tool


def _apply_goal(session: Session, dimension: LifeDimension, data: dict[str, Any]) -> Goal:
    goal = Goal(
        dimension_id=dimension.id,
        title=data["title"],
        horizon=_check_choice(data.get("horizon"), HORIZONS, "horizon"),
    )
    session.add(goal)
    session.flush()
    for need_data in data.get("needs", []):
        _apply_need(session, goal, need_data)
    return goal


def _apply_need(session: Session, goal: Goal, data: dict[str, Any]) -> Need:
    pain = data.get("pain")
    if pain is not None and not 0 <= int(pain) <= 10:
        raise ValueError("pain must be 0-10")
    need = Need(
        goal_id=goal.id,
        title=data["title"],
        horizon=_check_choice(data.get("horizon"), HORIZONS, "horizon"),
        pain_level=pain,
        openness_to_change=_check_choice(data.get("openness"), OPENNESS, "openness"),
    )
    session.add(need)
    session.flush()
    for activity_data in data.get("activities", []):
        _apply_activity(session, need, activity_data)
    return need


def _apply_activity(session: Session, need: Need, data: dict[str, Any]) -> Activity:
    activity = Activity(
        need_id=need.id,
        title=data["title"],
        cadence=_check_choice(data.get("cadence"), CADENCES, "cadence"),
    )
    session.add(activity)
    session.flush()
    for task_data in data.get("tasks", []):
        task = Task(activity_id=activity.id, title=task_data["title"])
        session.add(task)
        session.flush()
        for tool_name in task_data.get("tools", []):
            tool = _get_or_create_tool(session, str(tool_name))
            session.add(TaskTool(task_id=task.id, tool_id=tool.id))
    return activity


def _apply_network(session: Session, data: dict[str, Any], summary: IntakeSummary) -> None:
    for person_data in data.get("people", []):
        person = Person(display_name=person_data["name"], notes=person_data.get("notes"))
        session.add(person)
        session.flush()
        link = NetworkLink(
            person_id=person.id,
            role=person_data.get("role"),
            frequency=person_data.get("frequency"),
            recency=person_data.get("recency"),
            dimension_code=person_data.get("dimension"),
            notes=person_data.get("link_notes"),
        )
        session.add(link)
        summary.people += 1
        summary.network_links += 1
    for org_data in data.get("organizations", []):
        org = Organization(name=org_data["name"], notes=org_data.get("notes"))
        session.add(org)
        session.flush()
        link = NetworkLink(
            organization_id=org.id,
            role=org_data.get("role"),
            frequency=org_data.get("frequency"),
            recency=org_data.get("recency"),
            dimension_code=org_data.get("dimension"),
            notes=org_data.get("link_notes"),
        )
        session.add(link)
        summary.organizations += 1
        summary.network_links += 1


def apply_intake_dict(session: Session, data: dict[str, Any]) -> IntakeSummary:
    """Apply one intake document (parsed YAML) to the Profile. Rolls back on any error."""
    summary = IntakeSummary()
    try:
        for dim_data in data.get("dimensions", []):
            dimension = _get_or_create_dimension(session, dim_data["code"])
            satisfaction = dim_data.get("satisfaction")
            if satisfaction is not None:
                dimension.update_satisfaction(int(satisfaction))
            summary.dimensions += 1
            for goal_data in dim_data.get("goals", []):
                _apply_goal(session, dimension, goal_data)
                summary.goals += 1
                summary.needs += sum(len(g.get("needs", [])) for g in [goal_data])
                for need_data in goal_data.get("needs", []):
                    summary.activities += len(need_data.get("activities", []))
                    for act in need_data.get("activities", []):
                        summary.tasks += len(act.get("tasks", []))
                        for t in act.get("tasks", []):
                            summary.tools += len(set(t.get("tools", [])))
        _apply_network(session, data.get("network", {}), summary)
        session.commit()
    except Exception:
        session.rollback()
        raise
    return summary


def apply_intake_file(session: Session, path: str | Path) -> IntakeSummary:
    raw = Path(path).read_text(encoding="utf-8")
    return apply_intake_dict(session, yaml.safe_load(raw) or {})
