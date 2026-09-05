# 13 — Cold-start harness + go-live gate

**What to build:** A shadow mode where the judge runs and Digests assemble but nothing is delivered; a spot-check view for reviewing shadow scores against reality; UNSCORED-labeled urgent Watchlist notices (deprecation/security) that may surface during cold start; and a Profile coverage check that gates flipping the system live.

**Blocked by:** 05 — Digest assembly + 34-cell threshold matrix; 08 — Extraction tranche 1.

**Status:** resolved

- [x] Shadow mode: judging and digest assembly run, delivery suppressed
- [x] Spot-check view lets the user review shadow scores against reality
- [x] Urgent Watchlist notices can surface during cold start, labeled UNSCORED
- [x] Coverage check defines go-live; the system flips live only when it passes

## Comments

- Implemented in commit 0a440e5 (github.com/oraekene/personal-relevance-engine).
- Mode state machine in `system_flags` ('shadow' default, 'live'); `pre mode` shows mode + gate status with per-criterion failures.
- UNSCORED pattern: urgent Watchlist Changes (deprecation/security flags from the Change parser) surface into the daily digest labeled UNSCORED even before calibration — deduped across cycles. Mirrors the UNTRUSTED-param doctrine from the radar system.
- Coverage gate: ≥8/17 Life Dimensions touched (interview or extraction), ≥10 corpus Changes, ≥5 judged — explicit failure reasons printed; `go_live` raises until the gate passes, then flips the mode.
- Spot-check view: judge scores grouped into bands with reasoning so the user can sanity-check calibration before trusting digests (`pre spot-check`).
- Shadow semantics: `assemble_digest(shadow=True)` never marks items delivered (delivery itself arrives with ticket 06's push links).
- Verification: 9 tests; full suite 113 passing, mypy strict clean, ruff clean; smoke run verified shadow refusal of go-live on a thin profile and matrix override rendering.
