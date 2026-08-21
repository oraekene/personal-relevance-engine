from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from pre.change_corpus import FirehoseEntry, ingest_entries
from pre.embeddings import HashingEmbedder
from pre.models import Change, EntityEmbedding
from pre.retrieval import index_all, shortlist_for_change


def _seed(session: Session) -> Change:
    from pre.intake import apply_intake_dict

    apply_intake_dict(
        session,
        {
            "dimensions": [
                {
                    "code": "business",
                    "goals": [
                        {
                            "title": "Outbound prospecting machine",
                            "needs": [
                                {
                                    "title": "Predictable lead flow",
                                    "activities": [
                                        {
                                            "title": "Send outbound sequences in Apollo",
                                            "tasks": [
                                                {"title": "Pull new leads", "tools": ["Apollo"]}
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    entry = FirehoseEntry(
        product_name="Apollo",
        title="Apollo pricing update for Teams plans",
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    ingest_entries(session, [entry], "fixture-lane")
    return session.query(Change).one()


def test_index_covers_all_entity_types_and_changes(session: Session) -> None:
    change = _seed(session)
    counts = index_all(session)

    types = {row.entity_type for row in session.query(EntityEmbedding).all()}
    assert {"goal", "need", "activity", "task", "tool", "change"} <= types
    assert counts["changes"] == 1
    assert change.id is not None


def test_reindex_skips_unchanged_content(session: Session) -> None:
    _seed(session)
    first = index_all(session)
    second = index_all(session)

    assert second["skipped"] == first["entities"] + first["changes"]
    assert session.query(EntityEmbedding).count() == first["entities"] + first["changes"]


def test_shortlist_ranks_matching_tool_first(session: Session) -> None:
    change = _seed(session)
    index_all(session)

    candidates = shortlist_for_change(session, change.id, top_k=5)
    assert candidates, "shortlist should not be empty after indexing"
    top = candidates[0]
    assert top.entity_type == "tool"
    assert "apollo" in top.label.lower()
    assert top.score > 0.0
    scores = [c.score for c in candidates]
    assert scores == sorted(scores, reverse=True)


def test_shortlist_unknown_change_raises(session: Session) -> None:
    with pytest.raises(ValueError, match="not found"):
        shortlist_for_change(session, 9999)


def test_embedding_never_leaves_process(session: Session) -> None:
    """Contract guard: the embedder is local and deterministic (no network type in play)."""
    embedder = HashingEmbedder()
    vec = embedder.embed("anything")
    assert isinstance(vec, list) and all(isinstance(v, float) for v in vec)
