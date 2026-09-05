"""Profile navigation: one seam for hierarchy reads (issue 18, arch review C1).

Five modules (retrieval, judge, digest, coverage, view) each carried their own copy
of the Goal → Need → Activity → Task → Tool walk, plus Network special-cases.
This module owns all of it behind four functions. Writes stay out (intake, queue).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import (
    Activity,
    Goal,
    LifeDimension,
    Need,
    Organization,
    Person,
    Task,
    TaskTool,
    Tool,
)

ENTITY_MODELS: dict[str, Any] = {
    "goal": Goal,
    "need": Need,
    "activity": Activity,
    "task": Task,
    "tool": Tool,
    "person": Person,
    "organization": Organization,
}

DEFAULT_STALENESS_DAYS = 90


def get_row(session: Session, entity_type: str, entity_id: int) -> Any | None:
    """Fetch one Profile/Network row by type; None for unknown types and ids."""
    model = ENTITY_MODELS.get(entity_type)
    if model is None:
        return None
    return session.get(model, entity_id)


def _dimension_code_for_activity(session: Session, activity_id: int) -> str | None:
    """Walk Activity -> Need -> Goal -> Life Dimension (moved here from coverage)."""
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


def dimension_of(session: Session, entity_type: str, entity_id: int) -> str | None:
    """Walk an entity up to its Life Dimension code (Network entities have none)."""
    if entity_type == "goal":
        row = session.get(Goal, entity_id)
        dim = session.get(LifeDimension, row.dimension_id) if row else None
        return dim.code if dim else None
    if entity_type == "need":
        need = session.get(Need, entity_id)
        goal = session.get(Goal, need.goal_id) if need else None
        dim = session.get(LifeDimension, goal.dimension_id) if goal else None
        return dim.code if dim else None
    if entity_type == "activity":
        return _dimension_code_for_activity(session, entity_id)
    if entity_type == "task":
        task = session.get(Task, entity_id)
        if task is None:
            return None
        return _dimension_code_for_activity(session, task.activity_id)
    if entity_type == "tool":
        # Walk Tool -> Task -> Activity -> Need -> Goal -> Dimension via its uses.
        links = session.scalars(
            select(TaskTool).where(TaskTool.tool_id == entity_id)
        ).all()
        for link in links:
            code = dimension_of(session, "task", link.task_id)
            if code:
                return code
        return None
    return None  # person / organization: no dimension walk


def label_of(session: Session, entity_type: str, entity_id: int) -> str:
    """Short display label (digest semantics, byte-identical).

    Hierarchy entities show their title/name; Network entities and unknown ids
    fall back to type:id — Digest items can point at scored Network entities.
    """
    if entity_type not in ("goal", "need", "activity", "task", "tool"):
        return f"{entity_type}:{entity_id}"
    row = get_row(session, entity_type, entity_id)
    if row is None:
        return f"{entity_type}:{entity_id}"
    return str(getattr(row, "title", None) or getattr(row, "name", "") or "")


def _format_text(entity_type: str, row: Any) -> str:
    if entity_type == "activity":
        # Trailing space when cadence is missing is load-bearing: embedding
        # content hashes must not change, or every entity re-indexes.
        return f"{row.title} {row.cadence or ''}"
    if entity_type in ("tool", "organization"):
        return str(row.name)
    if entity_type == "person":
        return str(row.display_name)
    return str(row.title)


def text_of(session: Session, entity_type: str, entity_id: int) -> str:
    """Embeddable text for one entity (retrieval semantics, byte-identical)."""
    if entity_type not in ENTITY_MODELS:
        raise ValueError(f"unknown entity type {entity_type!r}")
    row = get_row(session, entity_type, entity_id)
    if row is None:
        raise ValueError(f"{entity_type} #{entity_id} not found")
    return _format_text(entity_type, row)


def iter_texts(session: Session) -> list[tuple[str, int, str]]:
    """(type, id, text) for every Profile/Network entity, in index order."""
    out: list[tuple[str, int, str]] = []
    for entity_type, model in (
        ("goal", Goal),
        ("need", Need),
        ("activity", Activity),
        ("task", Task),
        ("tool", Tool),
        ("person", Person),
        ("organization", Organization),
    ):
        rows: Any = session.scalars(select(model)).all()
        for row in rows:
            out.append((entity_type, row.id, _format_text(entity_type, row)))
    return out


def staleness_cutoff(now: datetime | None = None) -> datetime:
    raw = os.environ.get("PRE_STALENESS_DAYS", "")
    try:
        days = int(raw) if raw else DEFAULT_STALENESS_DAYS
    except ValueError:
        days = DEFAULT_STALENESS_DAYS
    return (now or datetime.now(UTC)) - timedelta(days=days)


def is_stale(
    session: Session, entity_type: str, entity_id: int, now: datetime | None = None
) -> bool:
    """True when the matched assertion hasn't been confirmed within the window.

    Hierarchy entities only: Network people/organizations always read fresh,
    matching the legacy digest behavior (they never entered the staleness map).
    """
    if entity_type not in ("goal", "need", "activity", "task", "tool"):
        return False
    row = get_row(session, entity_type, entity_id)
    if row is None:
        return False
    confirmed: datetime | None = getattr(row, "last_confirmed_at", None)
    if confirmed is None:
        return False
    if confirmed.tzinfo is None:
        confirmed = confirmed.replace(tzinfo=UTC)  # SQLite returns naive datetimes
    return confirmed < staleness_cutoff(now)


__all__ = [
    "DEFAULT_STALENESS_DAYS",
    "dimension_of",
    "get_row",
    "is_stale",
    "iter_texts",
    "label_of",
    "staleness_cutoff",
    "text_of",
]
