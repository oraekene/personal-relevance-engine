# 21 — Single ingestion adapter (C2)

## Problem Statement

Four importers (`tranche1/2/3`, `live`) each carry their own ~20 lines of sync-state fetch-or-create, propose-count loop, and `records_seen` stamping — already drifted (tranche2 counts pre-filter, the rest post-parse; tranche3 carries a tautology; only tranche2 filters Tools already owned; four result shapes; four CLI print formats). Adding a fifth source means a fifth copy. (Architecture review C2; grill settled the design.)

## Solution

A template-method importer hierarchy in `src/pre/ingest.py`: the base owns sync/propose/count/filter/result, one `LiveImporter` subclass overrides the post-import hook (auto-accept), and a registry maps all twelve kinds to instances. Per-tier cutovers, each tier fully old or fully new at every commit, suite green throughout.

## Commits

- 1. Base importer + unified `ImportResult` + registry with tranche1 kinds; CLI `import` switched; `tranche1.py` orchestration deleted (its parsers already live in `parsers.py`); tranche1 tests moved to the new entry.
- 2. Tranche2 + tranche3 kinds over (tautology deleted, known-tools filter universal); CLI `import2`/`import3` switched; old orchestration + `PARSERS` dicts deleted (parsers stay); ten-tiers test rewritten against the unified registry.
- 3. Live kinds over via the auto-accept hook; CLI `import-live` switched; old orchestration + `KINDS` deleted (parsers stay).

## Decision Document

- Shape: importer classes with template method (grill Q1, user pick) — kept minimal: one base, one `LiveImporter` subclass; per-kind variance that is pure data (tier strings) stays constructor data, not subclasses.
- Result: single `ImportResult` dataclass (tranche1's plus `skipped_known`/`auto_accepted` defaults); uniform CLI summary (grill Q2) — no test pins print text.
- Filter: universal known-tools filter as base behavior (grill Q3) — ticket 09 states it as a general criterion; tranche1/3/live queues get quieter; suite shows exact deltas.
- Keys/counts: registry keyed by the 12 kind names; live declares the `live-` tier prefix; `records_seen` counts what reached `propose()` with skipped reported separately; tautology deleted (grill Q4).
- Migration: per-tier cutovers as above (grill Q5) — each tier fully old or fully new per commit.

## Testing Decisions

- Good tests assert external behavior (proposals queued, counts, idempotent deltas, auto-accept audit), never implementation details.
- Existing tranche/live/network suites move to the new entry with attribute access; assertions hold (profiles start empty, so the newly-universal filter drops nothing in fixtures) except where the suite itself reports honest deltas.
- Regression net: full suite green on every commit.
- Prior art: full-history-then-delta idempotency tests, auto-accept audit test.

## Out of Scope

- Parser moves — pure parse functions stay in their modules.
- New source tiers or new link-evidence rules.
- Remaining candidates C4, C6.

**Status:** resolved

## Comments

- Implemented in commits 75d62ea (1: base + ImportResult + tranche1 over) → c14898d (tranche1 orphan removal) → 38817aa (2: tranche2+3 over, tautology deleted, filter universal) → ce7abda (3: live over via `LiveImporter.post_import`, `KINDS` + old entries deleted).
- Shape note: variance collapsed further than grilled — universal filter (no opt-in flag) and single result shape leave exactly one behavioral override, so the hierarchy is one base plus one `LiveImporter`; tier strings stay constructor data. Registry holds all 14 kinds; parsers never moved.
- Universal filter caused zero test deltas (fixtures run against empty/tool-less profiles); `records_seen` now counts what reached `propose()` everywhere.
- Review follow-ups (same turn): CLI print helper now returns str (`render_import_result`, killing a Data Clump — tier/path already ride the result); registry test pins all 12 kinds incl. live; new live test fires the universal filter on tranche1/3/live paths for the first time (seeded Github skipped, person still queued); `LiveImporter` added to `__all__`; single-subclass hierarchy kept per the grill tripwire (collapse to a flag if no second behavioral subclass appears). Deferred to C4: deriving argparse kind-choices from the registry so a new kind stops touching three places.
- Verification: ten-tiers test rewritten against the unified registry; full suite 174 passing on every commit, mypy strict, ruff clean.
