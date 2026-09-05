# 05 — Digest assembly + 34-cell threshold matrix

**What to build:** Judged Changes flow through a threshold matrix of digest type × Life Dimension (2 × 17 = 34 cells), all initialized at digest defaults (daily = precise, weekly = exploratory). The daily Digest (≤5) and weekly Digest (≤20) are assembled, ranked, and shown on a dashboard — each item displaying the matched entity (Goal/Need/Activity/Tool/Network entry), the dimension, and the judge's reasoning.

**Blocked by:** 04 — Judge integration + cost meter.

**Status:** resolved

- [x] Threshold matrix holds 34 independently valued cells initialized at digest defaults
- [x] Daily Digest assembles at most 5 items passing precise thresholds
- [x] Weekly Digest assembles at most 20 items passing exploratory thresholds
- [x] Dashboard shows each item's matched entity, Life Dimension, and judge reasoning

## Comments

- Implemented in commit 0a440e5 (github.com/oraekene/personal-relevance-engine).
- Matrix: `threshold_cells` table with `tuning` provenance ('default' | 'calibrated' | 'manual'); `ensure_matrix` lazily creates missing cells at defaults; `set_cell` for calibration (ticket 07 will feed it from Verdicts) and hand overrides; `pre matrix` renders all 34 and accepts `--set KIND:DIM SCORE`.
- Assembly: walks ChangeScores best-per-change, resolves the matched entity's Life Dimension — including the full Tool→Task→Activity→Need→Goal walk so dimension-specific thresholds apply to Tools — keeps only items passing their cell, ranks by score, caps at digest limits.
- Undelivered digests of a kind are replaced on re-assembly; `DigestItem.unscored` flag reserved for ticket 13's urgent notices; `pre digest --kind daily|weekly [--limit N]`.
- Verification: 9 tests (defaults, overrides, cell gating, cap-at-5 ordering, replacement, rendering); full suite 113 passing, mypy strict clean, ruff clean.
