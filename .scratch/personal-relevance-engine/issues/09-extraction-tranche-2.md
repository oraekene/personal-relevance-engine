# 09 — Extraction tranche 2: comms, notes, social

**What to build:** Parsers/loaders for the second tranche — communications metadata/content, productivity and notes systems, and social/content exports — feed the same confirmation queue. API-based sources mine MIT-licensed LlamaHub loaders per `research/open-source-connector-ecosystems.md`; proposals deduplicate against assertions already in the Profile.

**Blocked by:** 08 — Extraction tranche 1.

**Status:** resolved

- [x] Loaders handle comms, productivity/notes, and social/content sources
- [x] All proposals route through the same confirmation queue
- [x] Proposals deduplicate against assertions already in the Profile
- [x] Full history ingested on first connect, then deltas

## Comments

- Implemented in commit 06f577e (github.com/oraekene/personal-relevance-engine).
- Canonical export shapes documented in tranche2.py: comms JSON (from/to/date), notes JSON (app + pages), social JSON (platform + interactions). Correspondents come from the 'from' side only, with placeholder-name filtering (the user appears in 'to' of every message).
- Assertion types: People into the Network cluster (comms senders, social interaction partners) and Tools (notes app, social platform, known vendors named in note titles).
- Profile dedup: proposals for Tools already in the Profile are skipped at import time (`skipped_already_in_profile` in the result); queue-level uniqueness handles cross-run idempotency.
- Same SourceSyncState mechanics as tranche 1: full history on first connect, deltas after.
- Verification: 8 tests; full suite 71 passing, mypy strict, ruff clean.
