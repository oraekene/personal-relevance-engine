# 03 — Embeddings + retrieval shortlist

**What to build:** A local embedding model embeds Profile entities (Goals, Needs, Activities, Tasks, Tools), Network entities, and parsed Changes into pgvector. For any Change in the corpus, the user can see a ranked shortlist of candidate entities the Change plausibly relates to.

**Blocked by:** 01 — Profile schema + interview intake; 02 — Watchlist auto-seed + first Firehose adapter.

**Status:** resolved

- [x] Local embedding model computes vectors; no embedding content leaves the infrastructure
- [x] Profile entities, Network entities, and Changes are indexed in pgvector
- [x] Given a Change, retrieval returns a ranked shortlist of candidate entities with similarity scores
- [x] Shortlists are inspectable (CLI or minimal view) for spot-checking

## Comments

- Implemented in commit 06f577e (github.com/oraekene/personal-relevance-engine).
- Embedder v1: `HashingEmbedder` — deterministic feature-hashing with bigrams, L2-normalized, zero dependencies. Sits behind an `EmbeddingFunction` protocol so a stronger local model (sentence-transformers/Ollama) drops in without touching callers.
- Storage: `entity_embeddings` table with JSON vectors — runs identically on SQLite (dev/tests) and Postgres; on Postgres deployments migrate the column to pgvector `vector` for index-backed similarity (documented in models.py docstring).
- `index_all` covers goal/need/activity/task/tool/person/organization/change and skips unchanged texts via content hash.
- `shortlist_for_change` ranks by cosine; `pre index` / `pre shortlist <id> --top N` for inspection; judge-facing payload helper included for ticket 04.
- Verification: 10 tests (determinism, normalization, related-beats-unrelated ranking, coverage, idempotent re-index); full suite 71 passing, mypy strict, ruff clean.
