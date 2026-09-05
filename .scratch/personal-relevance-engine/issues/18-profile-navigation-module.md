# 18 — Deep Profile navigation module (C1)

## Problem Statement

Answering "which Life Dimension owns this entity, what is its label, is it stale?" requires bouncing between five copies of the Goal → Need → Activity → Task → Tool walk (retrieval, judge, digest, coverage, view), plus Network special-cases — including one module importing another's private helper to do it. Adding one Profile level touches all five. (Architecture review C1, verified against code; grill settled the design.)

## Solution

One deep Profile navigation module behind a four-function interface. Land it with its own tests, then migrate the five callers one per tiny commit, deleting each old walk as its caller migrates, suite green throughout.

## Commits

0. Merge the two `get_mode` definitions into one owner (coldstart keeps it; digest imports it) — drive-by review finding, no behavior change, existing cold-start tests cover it.
1. Add the Profile navigation module with `dimension_of` and `label_of` plus hierarchy-fixture tests covering all entity types (Network returns None dimension); no callers yet.
2. Add `text_of` and `is_stale` (staleness window owned here, explicit now-parameter) plus tests.
3. Migrate the retrieval caller to the new interface; delete its old walk copy; suite green.
4. Migrate the judge caller; delete its old context builder; suite green.
5. Migrate the digest caller (dimension, label, stale helpers); remove the private cross-module import; suite green.
6. Migrate the coverage caller; report output byte-identical; suite green.
7. Migrate the view caller; search proves no walk copies remain; full suite green.

## Decision Document

- Seam placement: new module (grill Q1) — the schema module is already a commit hot spot and cools to schema-only; model methods would scatter the walk across seven classes; the reporting module does not own navigation.
- Interface: `dimension_of` returns str-or-None (None for Network people/organizations, which sit outside the Life Dimension tree), `label_of`/`text_of`/`is_stale` cover all eight entity types (grill Q2).
- Migration: incremental per caller, suite green throughout; no big-bang (grill Q3).
- Scope: navigation reads only. Profile writes (intake, queue appliers, version bump) stay out under C3/issue 16 (grill Q4).
- dimension_code two-channel merge (proposal column vs payload key) is NOT decided here — it rides with issue 16, since the new interface returns None for Network and never serves link rows.

## Testing Decisions

- Good tests assert external behavior (digest contents, shortlist ranking, coverage counts, rendered profile text), never implementation details (prompt wording, embedding internals).
- New tests: navigation outputs on fixture hierarchies for all eight entity types, including Network-None and stale-window edges via the explicit now-parameter.
- Regression net: the existing 147-test suite stays green on every commit.
- Prior art: taxonomy fixture tests, retrieval ranking tests, cold-start shadow tests.

## Out of Scope

- Profile writes of any kind (intake, queue appliers, version bump) — see C3 / issue 16.
- The remaining architecture candidates C2, C4, C5 (beyond the get_mode merge), C6.
- New domain terms — "Profile" already exists in the glossary; no glossary change needed.

**Status:** ready-for-agent
