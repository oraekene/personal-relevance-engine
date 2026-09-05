# 14 — Ops baseline

**What to build:** The operational floor for the whole system: a cost dashboard summing judge spend against the monthly cap, provider-health monitoring that pages after 3 consecutive failures, nightly backups of the Profile and corpus with tested restore, and a corpus retention policy.

**Blocked by:** 04 — Judge integration + cost meter.

**Status:** resolved

- [x] Cost dashboard shows spend per period against the monthly cap
- [x] Provider-health monitor pages on 3 consecutive failures
- [x] Nightly backups of Profile and corpus run automatically with a tested restore
- [x] Corpus retention policy defined and enforced

## Comments

- Implemented in commit 12e3330 (github.com/oraekene/personal-relevance-engine).
- Ops module: `src/pre/ops.py` — `render_ops_dashboard` (MTD via ticket-04 `render_costs` + per-period `spend_by_month` YYYY-MM vs cap + provider/retention/backup sections), provider health in `SystemFlag` (`provider_consec_failures`, pages at 3, success resets, injectable `check_provider(probe)` so no network in tests), file-SQLite `backup_database`/`restore_database` + `mark_backup`/`last_backup_at`, retention `PRE_CORPUS_RETENTION_DAYS` (default 180d) with `prune_old_changes` preserving verdict-carrying Changes (VerdictLog is the calibration training signal; LLM cost history kept as audit).
- CLI: `pre ops` (dashboard), `pre backup --file` / `pre restore --file`, `pre prune`, `pre provider --result ok|fail` (exit 2 on page). Cron owns scheduling — commands are the cron entry points (same pattern as ticket 02 firehose).
- Code-review fixes before commit: backup handler copies first, stamps after (a failed copy never records success), under try/finally.
- Known limits (review-accepted): no cron/APScheduler wiring in-repo (deployment's job); per-period rows reuse the current cap; `pre backup` covers file-SQLite deployments — Postgres deployments use pg_dump from the same cron slot.
- Verification: 9 tests in tests/test_ops.py; full suite 147 passing, mypy strict, ruff clean, CLI smoke-tested end-to-end (ops/provider-paging/backup/restore/prune).
- Triage 2026-09-05: per-period cap history (spend_by_month reuses the live cap for all past periods) judged wontfix — the cap changes only via operator env edit, and the MTD invoice-protection surface always uses the live cap. Revisit if caps become scheduled/rotating. Postgres backup path → issue 15; verdict-retention cap → issue 17.
