# 10 — Network extraction

**What to build:** People, Organizations, and the user's relationship context to each are mined from comms, calendar, contacts, and social sources into the confirmation queue. Accepted entries populate the Network cluster so relationship-, family-, and social-dimension matching knows who the user interacts with and how often.

**Blocked by:** 09 — Extraction tranche 2.

**Status:** resolved

- [x] People and Organizations extracted from comms, calendar, contacts, and social sources
- [x] Relationship context captured per entity (frequency, recency, role, dimension link)
- [x] Proposals route through the confirmation queue with provenance
- [x] Network entities are embedded and retrievable in the matching pipeline

## Comments

- Implemented in commit 64d48ba (github.com/oraekene/personal-relevance-engine).
- Relationship context: person proposals now carry frequency buckets (weekly/monthly/adhoc from occurrence counts) and recency buckets (this-week/this-month/this-quarter/stale from event dates), plus role and Life Dimension link where known.
- New `contacts` source kind (canonical contacts JSON with organization/title/dimension) joins comms/social/calendar as Network evidence.
- `accept()` on a person proposal writes their NetworkLink once per evidence tier (repeat acceptance across tiers accumulates evidence, not duplicates).
- Retrieval integration verified by test: accepted People are indexed and appear in Change shortlists.
- Verification: 8 tests; full suite 95 passing, mypy strict clean, ruff clean.
