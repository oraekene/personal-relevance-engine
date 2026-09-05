# 07 — Calibration loop

**What to build:** Accumulated Verdicts re-fit the 34 threshold cells so each digest × dimension combination personalizes over time. The user can hand-override any cell. Profile assertions past their staleness window are flagged, and Digest items judged against stale assertions carry a visible staleness label.

**Blocked by:** 06 — Verdict capture + anywhere access.

**Status:** resolved

- [x] Verdict history re-fits threshold cells automatically
- [x] Each cell can be hand-overridden; overrides win over calibration
- [x] Stale Profile assertions are flagged
- [x] Items judged against stale assertions carry a visible staleness label

## Comments

- Implemented in commit 4b3580a (github.com/oraekene/personal-relevance-engine).
- Calibration heuristic (starting parameters): ≥4 verdicts per cell to move; dismissal rate >60% raises the floor by 5; <25% lowers it by 5; clamped 20–95. Cells with tuning='manual' are never touched — overrides win.
- Calibration samples VerdictLog directly: kind + dimension are snapshotted per verdict, so the training signal survives DigestItem cleanup. Assembly now preserves verdict-carrying items (history) and skips already-represented changes.
- Staleness: PRE_STALENESS_DAYS window (default 90d) against last_confirmed_at; `assemble_digest` stamps matched-entity staleness onto items; renders as [STALE PROFILE] next to the score.
- CLI: `pre calibrate`, `pre verdict <id> act|dismiss [--channel]`, `pre verdicts` (recent summary).
- Verification: 11 tests across calibration + staleness rendering; full suite 138 passing, mypy strict clean, ruff clean.
