"""Persist and inspect judge verdicts per Change (stage-3 output)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.judge import Judge, JudgeVerdict
from pre.models import Change, ChangeScore
from pre.retrieval import ShortlistCandidate, shortlist_for_change


def store_verdicts(
    session: Session, change_id: int, verdicts: list[JudgeVerdict], judge_name: str
) -> int:
    """Upsert one ChangeScore per (change, entity); returns rows written."""
    written = 0
    for verdict in verdicts:
        existing = session.scalar(
            select(ChangeScore).where(
                ChangeScore.change_id == change_id,
                ChangeScore.entity_type == verdict.entity_type,
                ChangeScore.entity_id == verdict.entity_id,
            )
        )
        if existing is None:
            session.add(
                ChangeScore(
                    change_id=change_id,
                    entity_type=verdict.entity_type,
                    entity_id=verdict.entity_id,
                    score=verdict.score,
                    reasoning=verdict.reasoning,
                    judge_name=judge_name,
                    call_id=verdict.call_id,
                )
            )
        else:
            existing.score = verdict.score
            existing.reasoning = verdict.reasoning
            existing.judge_name = judge_name
            existing.call_id = verdict.call_id
        written += 1
    session.commit()
    return written


def judge_change(session: Session, change_id: int, judge: Judge, top_k: int = 8) -> int:
    """Full stage 2→3 run for one Change: shortlist then judge then store."""
    change = session.get(Change, change_id)
    if change is None:
        raise ValueError(f"change {change_id} not found")
    candidates: list[ShortlistCandidate] = shortlist_for_change(session, change_id, top_k=top_k)
    if not candidates:
        return 0
    verdicts = judge.score(session, change, candidates)
    return store_verdicts(session, change_id, verdicts, judge.name)


def scores_for_change(session: Session, change_id: int) -> list[ChangeScore]:
    return list(
        session.scalars(
            select(ChangeScore)
            .where(ChangeScore.change_id == change_id)
            .order_by(ChangeScore.score.desc())
        ).all()
    )


def render_scores(session: Session, change_id: int) -> str:
    change = session.get(Change, change_id)
    if change is None:
        return f"change #{change_id} not found"
    lines = [
        f"SCORES for #{change.id} ({change.product_name}): {change.title}",
        "=" * 60,
    ]
    scores = scores_for_change(session, change_id)
    if not scores:
        lines.append("  (not judged yet — run `pre judge`)")
    for row in scores:
        lines.append(f"  {row.score:>3}/100 [{row.entity_type} #{row.entity_id}] {row.reasoning}")
        lines.append(f"      judge={row.judge_name}")
    return "\n".join(lines)
