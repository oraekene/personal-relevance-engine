# 04 — Judge integration + cost meter

**What to build:** Shortlisted Change × Profile pairs are scored by a real LLM judge behind an interface (per ADR-0002), returning a relevance score plus human-readable reasoning, stored on the Change. Every judge call is cost-metered with a monthly cap and a pre-invoice alert.

**Blocked by:** 03 — Embeddings + retrieval shortlist.

**Status:** resolved

- [x] Judge sits behind an interface so tests substitute a scripted judge (judge seam)
- [x] Judge receives full Profile context for the shortlisted entities and returns score + reasoning
- [x] Scores and reasoning are stored and inspectable per Change
- [x] Cost meter logs every call; monthly cap triggers an alert before the invoice

## Comments

- Implemented in commit 64d48ba (github.com/oraekene/personal-relevance-engine).
- Judge seam: `Judge` protocol with `ScriptedJudge` test double; `LLMJudge` targets any OpenAI-compatible endpoint via env config (PRE_LLM_API_KEY / PRE_LLM_BASE_URL / PRE_LLM_MODEL) and is never exercised in tests.
- Prompt construction and verdict parsing are pure functions (unit-tested): fenced JSON tolerated, scores clamped 0–100, out-of-range indexes dropped.
- Full Profile context per candidate per decision 8: Needs carry pain/openness, Activities cadence, Tools framed as relied-on.
- Storage: `change_scores` table upserts one row per Change × entity with reasoning, judge name, and the linked cost-log row.
- Cost doctrine enforced in code: `LLMCallLog` records every call; `enforce_budget` raises BudgetExceeded BEFORE the request is sent at ≥100% of cap; 80% watermark warning surfaced in `pre costs`.
- CLI: `pre judge <id> [--top N] [--llm]` (default offline demo judge so the pipeline runs without credentials), `pre costs`.
- Verification: 8 tests; full suite 95 passing, mypy strict clean, ruff clean.
