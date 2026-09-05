# 02 — Watchlist auto-seed + first Firehose adapter

**What to build:** Every Tool recorded in the Profile automatically appears on the Watchlist. One real Firehose adapter (a changelog/launch aggregator) ingests raw announcements on a schedule, and the Change parser structures each into a Change record — product, change type, affected users, pricing/deprecation/security flags — stored in the corpus. Near-duplicate Changes (same change reported by multiple sources) are deduped.

**Blocked by:** 01 — Profile schema + interview intake.

**Status:** resolved

- [x] Tools in the Profile seed the Watchlist automatically and stay in sync as Tools change
- [x] One Firehose adapter pulls real announcements on a cron schedule
- [x] The Change parser produces structured Change records with type and flags
- [x] Near-duplicate Changes are deduped to one corpus entry with source list
- [x] Parsed Changes are stored in the corpus and inspectable

## Comments

- Implemented in commit 133a190 (github.com/oraekene/personal-relevance-engine).
- Watchlist: `sync_watchlist` is idempotent — new Tools activate, deleted Tools deactivate (with timestamp). Runs after intake and after every accepted proposal.
- Firehose adapter: stdlib RSS/Atom reader (`pre ingest-firehose --url --source`). Cron invocation is the deployment's job (ticket 14 ops); the command is designed as the cron entry point.
- Classification: keyword heuristics with precedence security > deprecation > pricing > policy > improvement > feature; pricing/deprecation/security carried as boolean flags per spec.
- Dedup: normalized product+title sha256 fingerprint; same Change from another lane appends its source to the row instead of duplicating. Re-ingesting an identical feed is a no-op.
- Inspection: `pre changes` lists the corpus with type, flags, and lanes.
- Verification: covered by 12 tests in test_change_corpus.py; full suite 44 passing, mypy strict, ruff clean.
