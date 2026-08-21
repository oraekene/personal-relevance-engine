"""Render the Profile as a readable tree."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import Activity, Goal, LifeDimension, Need, NetworkLink, Task, TaskTool, Tool


def render_profile(session: Session) -> str:
    lines: list[str] = []
    dimensions = session.scalars(
        select(LifeDimension).order_by(LifeDimension.id)
    ).all()

    lines.append("PROFILE")
    lines.append("=" * 60)
    for dim in dimensions:
        score = dim.satisfaction_score
        score_text = f" [satisfaction {score}/10]" if score is not None else ""
        lines.append(f"\n# {dim.name} ({dim.code}){score_text}")
        goals = session.scalars(select(Goal).where(Goal.dimension_id == dim.id)).all()
        if not goals:
            lines.append("  (no goals recorded)")
        for goal in goals:
            horizon = f", {goal.horizon}" if goal.horizon else ""
            lines.append(f"  Goal: {goal.title}{horizon}")
            needs = session.scalars(select(Need).where(Need.goal_id == goal.id)).all()
            for need in needs:
                bits = []
                if need.horizon:
                    bits.append(need.horizon)
                if need.pain_level is not None:
                    bits.append(f"pain {need.pain_level}/10")
                if need.openness_to_change:
                    bits.append(f"openness {need.openness_to_change}")
                suffix = f" ({', '.join(bits)})" if bits else ""
                lines.append(f"    Need: {need.title}{suffix}")
                activities = session.scalars(
                    select(Activity).where(Activity.need_id == need.id)
                ).all()
                for activity in activities:
                    cadence = f" [{activity.cadence}]" if activity.cadence else ""
                    lines.append(f"      Activity: {activity.title}{cadence}")
                    tasks = session.scalars(
                        select(Task).where(Task.activity_id == activity.id)
                    ).all()
                    for task in tasks:
                        tool_names = [
                            tt.tool.name
                            for tt in session.scalars(
                                select(TaskTool).where(TaskTool.task_id == task.id)
                            ).all()
                        ]
                        tools = f"  (tools: {', '.join(tool_names)})" if tool_names else ""
                        lines.append(f"        Task: {task.title}{tools}")

    links = session.scalars(select(NetworkLink)).all()
    if links:
        lines.append("\nNETWORK")
        lines.append("-" * 60)
        for link in links:
            target = link.person.display_name if link.person else (
                link.organization.name if link.organization else "?"
            )
            bits = [b for b in (link.role, link.frequency, link.recency) if b]
            suffix = f" ({', '.join(bits)})" if bits else ""
            lines.append(f"  {target}{suffix}")
    return "\n".join(lines)


def render_tool_names(session: Session) -> list[str]:
    """Every distinct Tool in the Profile — the Watchlist seed list (ticket 02)."""
    return [name for (name,) in session.execute(select(Tool.name).order_by(Tool.name)).all()]
