"""Digest assembly: judged Changes -> ranked daily/weekly Digests via the threshold matrix.

Matrix: 2 digest kinds x 17 Life Dimensions = 34 cells. Cells initialize at digest
defaults (daily = precise, weekly = exploratory); calibration (ticket 07) and hand
overrides tune individual cells.

Assembly walks every ChangeScore, resolves the matched entity's Life Dimension, keeps
items whose score passes their cell, dedupes per Change (highest score wins), and caps
at the digest's item limit.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import (
    Activity,
    Change,
    ChangeScore,
    DigestItem,
    Goal,
    LifeDimension,
    Need,
    Task,
    ThresholdCell,
    Tool,
)
from pre.taxonomy import DIMENSIONS

DIGEST_LIMITS = {"daily": 5, "weekly": 20}
DEFAULT_MIN_SCORES = {"daily": 80, "weekly": 50}


def ensure_matrix(session: Session) -> dict[tuple[str, str], ThresholdCell]:
    """Create any missing cells at digest defaults; return the full 34-cell map."""
    cells: dict[tuple[str, str], ThresholdCell] = {}
    for cell in session.scalars(select(ThresholdCell)).all():
        cells[(cell.digest_kind, cell.dimension_code)] = cell

    for kind in DIGEST_LIMITS:
        for dimension in DIMENSIONS:
            key = (kind, dimension.code)
            if key not in cells:
                cell = ThresholdCell(
                    digest_kind=kind,
                    dimension_code=dimension.code,
                    min_score=DEFAULT_MIN_SCORES[kind],
                )
                session.add(cell)
                session.flush()
                cells[key] = cell
    session.commit()
    return cells


def set_cell(
    session: Session, kind: str, dimension_code: str, min_score: int, tuning: str = "manual"
) -> ThresholdCell:
    """Calibrate or hand-override one cell."""
    if min_score < 0 or min_score > 100:
        raise ValueError("min_score must be 0-100")
    ensure_matrix(session)
    cell = session.scalars(
        select(ThresholdCell).where(
            ThresholdCell.digest_kind == kind,
            ThresholdCell.dimension_code == dimension_code,
        )
    ).one()
    cell.min_score = min_score
    cell.tuning = tuning
    session.commit()
    return cell


def render_matrix(session: Session) -> str:
    cells = ensure_matrix(session)
    lines = ["THRESHOLD MATRIX (min scores)", "=" * 60]
    lines.append(f"{'dimension':<28} {'daily':>6} {'weekly':>6}   tuning")
    for dimension in DIMENSIONS:
        daily = cells[("daily", dimension.code)]
        weekly = cells[("weekly", dimension.code)]
        tuning_text = "/".join(sorted({daily.tuning, weekly.tuning}))
        lines.append(
            f"{dimension.code:<28} {daily.min_score:>6} {weekly.min_score:>6}   {tuning_text}"
        )
    return "\n".join(lines)


def _dimension_for_entity(session: Session, entity_type: str, entity_id: int) -> str | None:
    """Walk an entity up to its Life Dimension code (Network entities have none yet)."""
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
        from pre.coverage import _dimension_code_for_activity

        return _dimension_code_for_activity(session, entity_id)
    if entity_type == "task":
        task = session.get(Task, entity_id)
        if task is None:
            return None
        from pre.coverage import _dimension_code_for_activity

        return _dimension_code_for_activity(session, task.activity_id)
    if entity_type == "tool":
        # Walk Tool -> Task -> Activity -> Need -> Goal -> Dimension via its strongest use.
        from sqlalchemy import select as _select

        from pre.models import TaskTool

        links = session.scalars(
            _select(TaskTool).where(TaskTool.tool_id == entity_id)
        ).all()
        for link in links:
            code = _dimension_for_entity(session, "task", link.task_id)
            if code:
                return code
        return None
    return None  # person / organization: no dimension walk yet


def _entity_label(session: Session, entity_type: str, entity_id: int) -> str:
    model = {
        "goal": Goal,
        "need": Need,
        "activity": Activity,
        "task": Task,
        "tool": Tool,
    }.get(entity_type)
    row = model and session.get(model, entity_id)
    if row is None:
        return f"{entity_type}:{entity_id}"
    label = str(getattr(row, "title", None) or getattr(row, "name", "") or "")
    return label


def assemble_digest(
    session: Session,
    kind: str,
    limit: int | None = None,
    shadow: bool = False,
) -> list[DigestItem]:
    """Assemble one Digest from judged Changes passing their dimension's cell.

    Re-running replaces the previous undelivered digest of the same kind. In shadow
    mode items are assembled but never marked delivered.
    """
    if kind not in DIGEST_LIMITS:
        raise ValueError(f"unknown digest kind {kind!r}")
    cap = limit if limit is not None else DIGEST_LIMITS[kind]
    cells = ensure_matrix(session)

    # Replace any previous undelivered digest of this kind.
    for old in session.scalars(
        select(DigestItem).where(
            DigestItem.digest_kind == kind, DigestItem.delivered_at.is_(None)
        )
    ).all():
        session.delete(old)
    session.flush()

    scored_rows = session.scalars(select(ChangeScore).order_by(ChangeScore.score.desc())).all()
    best_per_change: dict[int, ChangeScore] = {}
    for score_row in scored_rows:
        current = best_per_change.get(score_row.change_id)
        if current is None or score_row.score > current.score:
            best_per_change[score_row.change_id] = score_row

    items: list[DigestItem] = []
    for change_id, score_row in sorted(
        best_per_change.items(), key=lambda kv: kv[1].score, reverse=True
    ):
        dimension = _dimension_for_entity(session, score_row.entity_type, score_row.entity_id)
        cell_key = (kind, dimension) if dimension else (kind, "__network__")
        cell = cells.get(cell_key)
        min_score = cell.min_score if cell else DEFAULT_MIN_SCORES[kind]
        if score_row.score < min_score:
            continue

        item = DigestItem(
            digest_kind=kind,
            change_id=change_id,
            score=score_row.score,
            entity_type=score_row.entity_type,
            entity_id=score_row.entity_id,
            entity_label=_entity_label(session, score_row.entity_type, score_row.entity_id),
            dimension_code=dimension,
            reasoning=score_row.reasoning,
        )
        session.add(item)
        items.append(item)
        if len(items) >= cap:
            break
    session.commit()
    _ = shadow  # delivery marking arrives with ticket 06; shadow digests never deliver
    return items


def surface_unscored_urgent(session: Session, kind: str = "daily") -> int:
    """Cold start (ticket 13): urgent Watchlist Changes surface labeled UNSCORED."""
    added = 0
    for change in session.scalars(select(Change)).all():
        urgent = change.is_deprecation or change.is_security
        already = session.scalar(
            select(DigestItem).where(DigestItem.change_id == change.id)
        )
        if not urgent or already is not None:
            continue
        session.add(
            DigestItem(
                digest_kind=kind,
                change_id=change.id,
                score=0,
                entity_type="tool",
                entity_id=0,
                entity_label=change.product_name,
                reasoning="UNSCORED: urgent Watchlist notice surfaced during cold start",
                unscored=True,
            )
        )
        added += 1
    session.commit()
    return added


def render_digest(session: Session, kind: str) -> str:
    from pre.models import utcnow

    mode = get_mode(session)
    items = session.scalars(
        select(DigestItem).where(DigestItem.digest_kind == kind).order_by(DigestItem.score.desc())
    ).all()
    title = f"{kind.upper()} DIGEST" + (" [SHADOW MODE — nothing delivered]" if mode == "shadow" else "")
    lines = [title, "=" * 60]
    if not items:
        lines.append("  (nothing passed the thresholds)")
    for item in items:
        flag = " [UNSCORED]" if item.unscored else ""
        dim = f" ({item.dimension_code})" if item.dimension_code else ""
        lines.append(f"  {item.score:>3}/100{flag} {item.entity_label}{dim}")
        change = session.get(Change, item.change_id)
        if change:
            lines.append(f"      [{change.change_type}] {change.product_name}: {change.title}")
        lines.append(f"      why: {item.reasoning}")
    _ = utcnow
    return "\n".join(lines)


def get_mode(session: Session) -> str:
    from pre.models import SystemFlag

    flag = session.scalar(select(SystemFlag).where(SystemFlag.key == "mode"))
    return flag.value if flag else "shadow"


__all__ = [
    "DEFAULT_MIN_SCORES",
    "DIGEST_LIMITS",
    "assemble_digest",
    "ensure_matrix",
    "get_mode",
    "render_digest",
    "render_matrix",
    "set_cell",
    "surface_unscored_urgent",
]
