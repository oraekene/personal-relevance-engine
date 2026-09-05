# 17 — Verdict-carrying corpus retention cap

**Question to resolve:** `prune_old_changes` (`src/pre/ops.py`) permanently exempts any Change with a VerdictLog row or non-NULL DigestItem verdict, because VerdictLog is the calibration training signal. The corpus therefore grows without bound (slowly: single user, digest-scale rows). Options: (a) accept unbounded growth as the price of personalization; (b) time-box verdict retention (e.g. keep 365d of verdicts, prune older Change+Verdict rows together); (c) tiered policy (keep per-cell verdict aggregates, drop raw rows). (b)/(c) weaken calibration history; (a) costs storage only.

**Blocked by:** 14 — Ops baseline (prune exists).

**Status:** needs-triage

- [ ] Maintainer picks (a)/(b)/(c), with a day-count if boxed
- [ ] Implement the chosen policy + test (prune deletes X, preserves Y)
- [ ] Calibration still converges after prune (test: `calibrate_from_verdicts` on retained signal)
