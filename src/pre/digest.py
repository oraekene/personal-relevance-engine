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

from pre.models import Change, ChangeScore, DigestItem, ThresholdCell
from pre.profile import dimension_of, is_stale, label_of
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

    # Replace undelivered, unjudged items of this kind. Verdict-carrying items are
    # history — they survive re-assembly (their VerdictLog rows train calibration).
    for old in session.scalars(
        select(DigestItem).where(
            DigestItem.digest_kind == kind,
            DigestItem.delivered_at.is_(None),
            DigestItem.verdict.is_(None),
        )
    ).all():
        session.delete(old)
    session.flush()

    scored_rows = session.scalars(select(ChangeScore).order_by(ChangeScore.score.desc())).all()
    best_per_change: dict[int, ChangeScore] = {}
    existing_changes = {
        row.change_id
        for row in session.scalars(
            select(DigestItem).where(DigestItem.digest_kind == kind)
        ).all()
    }
    for score_row in scored_rows:
        if score_row.change_id in existing_changes:
            continue  # already represented (e.g. a verdicted historical item)
        current = best_per_change.get(score_row.change_id)
        if current is None or score_row.score > current.score:
            best_per_change[score_row.change_id] = score_row

    items: list[DigestItem] = []
    for change_id, score_row in sorted(
        best_per_change.items(), key=lambda kv: kv[1].score, reverse=True
    ):
        dimension = dimension_of(session, score_row.entity_type, score_row.entity_id)
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
            entity_label=label_of(session, score_row.entity_type, score_row.entity_id),
            dimension_code=dimension,
            reasoning=score_row.reasoning,
        )
        from pre.verdicts import get_profile_version

        item.profile_version = get_profile_version(session)
        item.stale = is_stale(session, score_row.entity_type, score_row.entity_id)
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
    from pre.coldstart import get_mode
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
        stale = " [STALE PROFILE]" if item.stale else ""
        dim = f" ({item.dimension_code})" if item.dimension_code else ""
        verdict = f" -> {item.verdict.upper()}" if item.verdict else ""
        lines.append(f"  {item.score:>3}/100{flag}{stale}{dim}{verdict} {item.entity_label}")
        change = session.get(Change, item.change_id)
        if change:
            lines.append(f"      [{change.change_type}] {change.product_name}: {change.title}")
        lines.append(f"      why: {item.reasoning}")
    _ = utcnow
    return "\n".join(lines)


__all__ = [
    "DEFAULT_MIN_SCORES",
    "DIGEST_LIMITS",
    "assemble_digest",
    "ensure_matrix",
    "render_digest",
    "render_matrix",
    "set_cell",
    "surface_unscored_urgent",
]
