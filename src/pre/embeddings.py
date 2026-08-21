"""Local embedding layer.

`HashingEmbedder` is a deterministic feature-hashing embedder: tokens are hashed into a
fixed-dimension bag-of-concepts vector (with bigrams), L2-normalized. It never sends
content anywhere (spec: no embedding content leaves the infrastructure) and has zero
model dependencies.

It implements `EmbeddingFunction`, so a stronger local model (e.g. a
sentence-transformers checkpoint served in-process) can replace it without touching any
caller: same protocol, same dimension contract per instance.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re

_TOKEN = re.compile(r"[a-z0-9]+")
_DIM = 256


def _bucket(token: str, dim: int) -> int:
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % dim


class HashingEmbedder:
    """Deterministic hashed-bag embedding. Same text -> same vector, always."""

    def __init__(self, dim: int = _DIM) -> None:
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _TOKEN.findall(text.lower())
        grams = [*tokens]
        grams.extend(f"{a}_{b}" for a, b in itertools.pairwise(tokens))
        for gram in grams:
            vec[_bucket(gram, self.dim)] += 1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0.0:
            return vec
        return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity for equal-length vectors (inputs expected L2-normalized)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
