# 11 — Extraction tranche 3: device, health/location, work systems

**What to build:** Parsers for the final tranche — device exhaust (browser history, app usage), health/location exports, and the user's own work systems (hermes agent logs, job-pipeline records) — completing all ten source tiers feeding the confirmation queue.

**Blocked by:** 09 — Extraction tranche 2.

**Status:** resolved

- [x] Parsers handle device exhaust, health/location, and work-system logs
- [x] All ten source tiers now feed the confirmation queue
- [x] Profile coverage report shows which Life Dimensions each tier has enriched

## Comments

- Implemented in commit 64d48ba (github.com/oraekene/personal-relevance-engine).
- Canonical shapes documented in tranche3.py: device JSON (app/domain/minutes), health JSON (apps + sessions), worksystems JSON (system/runs/last_run).
- Dimension hints ride on proposals (`ProposedAssertion.dimension_code`): health → physical_health, work systems → business, device → none. Hints are coverage signal, not hierarchy placement.
- Coverage report (`pre coverage`): per Life Dimension — goals/needs/activities/tasks counts plus which extraction tiers proposed assertions into it; untouched dimensions listed explicitly.
- All ten source tiers confirmed wired: takeout, financial, commerce (t1); comms, notes, social, contacts (t2); live-calendar, live-email (live); device, health, work-systems (t3).
- Verification: 8 tests; full suite 95 passing, mypy strict clean, ruff clean; smoke run exercised judge → costs → coverage end-to-end.
