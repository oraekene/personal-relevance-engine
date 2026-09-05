# 12 — Live connectors: calendar + email

**What to build:** The two fast-changing sources stream continuously into the Profile via live connectors, proposing updates through the confirmation-queue pattern, with a pre-approved auto-accept rule for low-risk, high-confidence update classes (audit-trailed). Both connectors pull full history on first connect, then deltas.

**Blocked by:** 08 — Extraction tranche 1.

**Status:** resolved

- [x] Calendar connector keeps Activities and cadences fresh
- [x] Email connector surfaces new Tools and commitments
- [x] First connect backfills full available history; subsequent runs delta
- [x] Live proposals follow the confirmation-queue pattern
- [x] Pre-approved low-risk update classes auto-accept with an audit trail

## Comments

- Implemented in commit 06f577e (github.com/oraekene/personal-relevance-engine).
- v1 contract: connectors consume canonical JSON documents (the shape a real Google Calendar / Gmail fetcher produces). Swapping in OAuth-backed fetchers later changes only the fetch step; parsing, queueing, auto-accept, and delta logic are here and tested.
- Calendar: repeated event titles propose Activities with cadence evidence (daily/weekly/monthly keyword hints + occurrence counts); one-off events ignored. Activity acceptance is deliberately manual — it needs a parent Need in the hierarchy (`accept` requires payload.need_id; refused otherwise).
- Email: senders with ≥2 messages propose People (Network); subjects naming known vendors propose Tools at 0.9 confidence.
- Auto-accept class: entity_type=tool AND confidence ≥ 0.85 → accepted with `decided_via='auto-rule'` + timestamp (audit trail on the proposal row). People and Activities always wait for a human.
- Delta: queue uniqueness makes every re-pull idempotent; SourceSyncState tracks first-connect vs delta per source.
- Verification: 9 tests; full suite 71 passing, mypy strict, ruff clean; CLI smoke run covered index → shortlist → import2 → import-live → queue end-to-end.
