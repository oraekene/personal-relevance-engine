# 08 — Extraction tranche 1: takeouts, financial, commerce

**What to build:** Parsers for the first three source tiers (platform takeouts, financial transactions, commerce/order history) extract candidate Profile assertions into a confirmation queue; the user accepts or rejects each proposal, and accepted assertions merge with full provenance. Export-format parsers (Takeout MBOX/JSON, bank CSVs, order exports) are written by us — no open-source project covers them; API-based sources mine MIT-licensed code (LlamaHub loaders, Meltano taps, MCP reference) per `research/open-source-connector-ecosystems.md`. Every source pulls its full available history on first connect, then deltas.

**Blocked by:** 01 — Profile schema + interview intake.

**Status:** resolved

- [x] Parsers handle batch exports for takeouts, financial, and commerce sources
- [x] First connect ingests full available history; subsequent runs delta
- [x] Extracted candidates land in a confirmation queue, never directly in the Profile
- [x] User can accept or reject each proposed assertion
- [x] Accepted assertions carry source, confidence, and confirmation timestamp

## Comments

- Implemented in commit 133a190 (github.com/oraekene/personal-relevance-engine).
- v1 parser contract: canonical export shapes documented in parsers.py (financial CSV date/description/amount, commerce CSV date/item/seller, Google Takeout MyActivity.json). Messy raw exports need a cleaning step before import.
- Assertion type this tranche: revealed Tool usage with recurrence-scaled confidence (0.5 base, +0.15 per recurrence, cap 0.95).
- Delta semantics: proposal uniqueness on (payload_key, source_tier) makes re-imports idempotent; repeat observations strengthen confidence instead of duplicating. SourceSyncState distinguishes first connect from deltas.
- accept writes a Tool with source=extraction:<tier> + timestamp, then auto-syncs the Watchlist (ticket 02 integration covered by test).
- Verification: 44 tests pass (14 new), mypy strict clean, ruff clean, CLI smoke-tested end-to-end including accept→Watchlist.
