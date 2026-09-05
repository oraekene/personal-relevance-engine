"""Retrieval: embed Profile, Network, and Change entities; shortlist candidates per Change.

Stage 2 of the matching funnel (ADR-0002): cheap embedding similarity shortlists the
candidate entities an LLM judge will later score (ticket 04+).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.embeddings import HashingEmbedder, content_hash, cosine
from pre.models import Change, EntityEmbedding
from pre.profile import iter_texts

Embedder = HashingEmbedder


@dataclass(frozen=True)
class IndexedEntity:
    entity_type: str
    entity_id: int
    text: str


@dataclass(frozen=True)
class ShortlistCandidate:
    entity_type: str
    entity_id: int
    label: str
    score: float


def _entity_text(session: Session) -> list[IndexedEntity]:
    """Collect the embeddable text for every Profile and Network entity."""
    return [IndexedEntity(t, i, tx) for (t, i, tx) in iter_texts(session)]


def index_all(session: Session, embedder: Embedder | None = None) -> dict[str, int]:
    """(Re)index every Profile/Network entity and corpus Change. Skips unchanged texts."""
    embedder = embedder or Embedder()
    counts = {"entities": 0, "changes": 0, "skipped": 0}

    def upsert(entity_type: str, entity_id: int, text: str) -> None:
        existing = session.scalar(
            select(EntityEmbedding).where(
                EntityEmbedding.entity_type == entity_type,
                EntityEmbedding.entity_id == entity_id,
            )
        )
        chash = content_hash(text)
        if existing is not None and existing.content_hash == chash:
            counts["skipped"] += 1
            return
        vector = embedder.embed(text)
        if existing is None:
            session.add(
                EntityEmbedding(
                    entity_type=entity_type,
                    entity_id=entity_id,
                    content_hash=chash,
                    dim=len(vector),
                    vector_json=vector,
                )
            )
        else:
            existing.content_hash = chash
            existing.dim = len(vector)
            existing.vector_json = vector

    for entity in _entity_text(session):
        upsert(entity.entity_type, entity.entity_id, entity.text)
        counts["entities"] += 1
    for change in session.scalars(select(Change)).all():
        upsert("change", change.id, f"{change.product_name} {change.title} {change.change_type}")
        counts["changes"] += 1
    session.commit()
    return counts


def shortlist_for_change(
    session: Session, change_id: int, top_k: int = 8, embedder: Embedder | None = None
) -> list[ShortlistCandidate]:
    """Rank Profile/Network entities against one Change by cosine similarity."""
    embedder = embedder or Embedder()
    change = session.get(Change, change_id)
    if change is None:
        raise ValueError(f"change {change_id} not found")
    query_vec = embedder.embed(f"{change.product_name} {change.title} {change.change_type}")

    labels: dict[tuple[str, int], str] = {}
    for entity in _entity_text(session):
        labels[(entity.entity_type, entity.entity_id)] = entity.text.split("\n")[0]

    candidates: list[ShortlistCandidate] = []
    rows = session.scalars(
        select(EntityEmbedding).where(EntityEmbedding.entity_type != "change")
    ).all()
    for row in rows:
        score = cosine(query_vec, row.vector_json)
        candidates.append(
            ShortlistCandidate(
                entity_type=row.entity_type,
                entity_id=row.entity_id,
                label=labels.get((row.entity_type, row.entity_id), "?"),
                score=round(score, 4),
            )
        )
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates[:top_k]


def render_shortlist(change: Change, candidates: list[ShortlistCandidate]) -> str:
    lines = [
        f"SHORTLIST for #{change.id} ({change.product_name}): {change.title}",
        "=" * 60,
    ]
    for rank, candidate in enumerate(candidates, start=1):
        lines.append(
            f"  {rank}. [{candidate.entity_type}] {candidate.label}  (score {candidate.score})"
        )
    if len(candidates) == 0:
        lines.append("  (no indexed entities — run `pre index` first)")
    return "\n".join(lines)


def to_payload(candidates: list[ShortlistCandidate]) -> list[dict[str, Any]]:
    """The judge-facing shape of a shortlist (ticket 04 consumes this)."""
    return [
        {"entity_type": c.entity_type, "entity_id": c.entity_id, "label": c.label, "score": c.score}
        for c in candidates
    ]
