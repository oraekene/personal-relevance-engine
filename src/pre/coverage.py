"""Profile coverage report: which Life Dimensions exist, and which tiers enriched them."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import (
    Activity,
    Goal,
    LifeDimension,
    Need,
    ProposedAssertion,
    Task,
)
from pre.taxonomy import DIMENSIONS


@dataclass
class DimensionCoverage:
    code: str
    name: str
    goals: int = 0
    needs: int = 0
    activities: int = 0
    tasks: int = 0
    tiers: set[str] = field(default_factory=set)


def _dimension_code_for_activity(session: Session, activity_id: int) -> str | None:
    """Walk Activity -> Need -> Goal -> Life Dimension."""
    from pre.models import LifeDimension

    activity = session.get(Activity, activity_id)
    if activity is None:
        return None
    need = session.get(Need, activity.need_id)
    if need is None:
        return None
    goal = session.get(Goal, need.goal_id)
    if goal is None:
        return None
    dim = session.get(LifeDimension, goal.dimension_id)
    return dim.code if dim else None


def coverage_report(session: Session) -> list[DimensionCoverage]:
    """Per Life Dimension: hierarchy counts + extraction tiers that touched it.

    A tier "enriches" a dimension when it proposed assertions carrying that
    dimension_code (accepted or not — the attempt is signal).
    """
    report: dict[str, DimensionCoverage] = {
        d.code: DimensionCoverage(code=d.code, name=d.name) for d in DIMENSIONS
    }

    for goal in session.scalars(select(Goal)).all():
        dim = session.get(LifeDimension, goal.dimension_id)
        code = dim.code if dim else None
        if code in report:
            report[code].goals += 1
    for need in session.scalars(select(Need)).all():
        parent_goal = session.get(Goal, need.goal_id)
        if parent_goal is None:
            continue
        dim = session.get(LifeDimension, parent_goal.dimension_id)
        code = dim.code if dim else None
        if code in report:
            report[code].needs += 1
    for activity in session.scalars(select(Activity)).all():
        code = _dimension_code_for_activity(session, activity.id)
        if code in report:
            report[code].activities += 1
    for task in session.scalars(select(Task)).all():
        code = (
            _dimension_code_for_activity(session, task.activity_id) if task.activity_id else None
        )
        if code in report:
            report[code].tasks += 1

    for prop in session.scalars(select(ProposedAssertion)).all():
        if prop.dimension_code and prop.dimension_code in report:
            report[prop.dimension_code].tiers.add(prop.source_tier)

    return [report[d.code] for d in DIMENSIONS]


def render_coverage(session: Session) -> str:
    lines = [
        "PROFILE COVERAGE (17 Life Dimensions)",
        "=" * 60,
        "format: goals/needs/activities/tasks + enrichment tiers",
    ]
    empty: list[str] = []
    for cov in coverage_report(session):
        counts = f"{cov.goals}/{cov.needs}/{cov.activities}/{cov.tasks}"
        if cov.goals == cov.needs == cov.activities == cov.tasks == 0 and not cov.tiers:
            empty.append(cov.name)
            continue
        tiers = ", ".join(sorted(cov.tiers)) if cov.tiers else "interview only"
        lines.append(f"  {cov.name:<28} {counts:>10}   tiers: {tiers}")
    if empty:
        lines.append(f"\n  untouched dimensions ({len(empty)}): {', '.join(empty)}")
    return "\n".join(lines)
