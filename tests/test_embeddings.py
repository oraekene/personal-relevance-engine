from __future__ import annotations

import pytest

from pre.embeddings import HashingEmbedder, content_hash, cosine


@pytest.fixture()
def embedder() -> HashingEmbedder:
    return HashingEmbedder(dim=256)


def test_deterministic(embedder: HashingEmbedder) -> None:
    assert embedder.embed("outbound prospecting daily") == embedder.embed(
        "outbound prospecting daily"
    )


def test_normalized_to_unit_length(embedder: HashingEmbedder) -> None:
    vec = embedder.embed("calibrated alert thresholds")
    norm = sum(v * v for v in vec) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)


def test_identical_texts_similarity_one(embedder: HashingEmbedder) -> None:
    a = embedder.embed("weekly pipeline review")
    assert cosine(a, a) == pytest.approx(1.0)


def test_related_text_outranks_unrelated(embedder: HashingEmbedder) -> None:
    query = embedder.embed("github pricing change for teams")
    related = embedder.embed("github pricing update for teams plans")
    unrelated = embedder.embed("dentist appointment reminder")
    assert cosine(query, related) > cosine(query, unrelated)


def test_content_hash_changes_with_text() -> None:
    assert content_hash("a") != content_hash("b")
    assert content_hash("same") == content_hash("same")
