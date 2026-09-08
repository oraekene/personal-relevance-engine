# 17 — Verdict-carrying corpus retention cap

**Question to resolve:** `prune_old_changes` (`src/pre/ops.py`) permanently exempts any Change with a VerdictLog row or non-NULL DigestItem verdict, because VerdictLog is the calibration training signal. The corpus therefore grows without bound (slowly: single user, digest-scale rows). Options: (a) accept unbounded growth as the price of personalization; (b) time-box verdict retention (e.g. keep 365d of verdicts, prune older Change+Verdict rows together); (c) tiered policy (keep per-cell verdict aggregates, drop raw rows). (b)/(c) weaken calibration history; (a) costs storage only.

**Blocked by:** 14 — Ops baseline (prune exists).

**Status:** resolved

- [x] Maintainer picks (a)/(b)/(c), with a day-count if boxed
- [x] Implement the chosen policy + test (prune deletes X, preserves Y)
- [x] Calibration still converges after prune (test: `calibrate_from_verdicts` on retained signal)

## Comments

- Decision 2026-09-05: **(a) accept unbounded growth** — keep every Verdict indefinitely.
- Rationale: single-person scale (a handful of Verdicts per day) keeps the table tiny for years; (b) would let quiet Life Dimensions forget learned tuning, (c) adds machinery for a storage problem that does not exist. No code change: `prune_old_changes` already preserves verdict-carrying Changes, and its docstring now records this decision. Revisit only if the database grows noticeably.
- Checkbox 2–3 intentionally unmarked: nothing to implement or prove.
- Proof pass 2026-09-05 (revisit): (a) stands, now evidenced. (1) Scale-convergence test (`test_calibration_converges_over_large_aged_history`): 12 real dismissals plus bulk backfill to 300 VerdictLog rows aged over 400 days; three calibration runs converge daily/business 80 → 95 with all 300 rows intact — old rows still train. (2) Growth visibility: `pre ops` now reports verdict count plus oldest-verdict date, so "grows noticeably" is observable. Aggregates (c) rejected on accuracy grounds: a lossy projection can at best tie raw rows, and loses time-weighting, per-change audit, and version-scoped retraining.
