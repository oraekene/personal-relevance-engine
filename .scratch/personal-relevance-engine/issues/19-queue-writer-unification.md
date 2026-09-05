# 19 — Queue/Profile-writer unification (C3)

## Problem Statement

`queue.accept` carries four near-identical application branches (tool/person/organization/activity) plus shared link-evidence helpers. Every new entity type edits the queue; the Network-link rule already lives in two copies. (Architecture review C3; grill settled the design.)

## Solution

A new writer seam (`src/pre/apply.py`) with an applier registry. Land appliers unwired one type per commit with direct unit tests, then flip `accept` to registry dispatch in one commit — small diffs and future bisect, never mixed mechanisms mid-flight.

## Commits

- A. New `apply.py` shell: shared link-evidence helpers moved from `queue.py` (queue imports them back); no appliers yet; suite green.
- B. Tool applier + direct unit test (apply + refuse paths); unwired; suite green.
- C. Person + organization appliers sharing one link writer + direct tests; unwired; suite green.
- D. Activity applier + direct test (missing need refuses); unwired; suite green.
- E. Flip: `accept` dispatches through the registry and the four inline branches are deleted; full suite proves equivalence.

## Decision Document

- Seam placement: new `src/pre/apply.py` (grill Q1) — registry in `queue.py` leaves the hot spot hot; `profile.py` stays reads-only per the C1 boundary.
- Applier interface: plain functions `(session, prop) -> applied | None`; None keeps today's refuse-and-stay-pending semantics (grill Q2).
- Version bump stays in `accept` as the last lifecycle step — a post-commit notification, not part of the write; moving it would drag the verdicts import over (grill Q3). `run_auto_accept` and `reject` are pure lifecycle and stay put.
- Migration: unwired per-type commits A–D plus one flip E (grill Q4 hybrid) — per-type diffs and bisectability without ever running mixed mechanisms; brief dead code in A–D is covered by direct tests.

## Testing Decisions

- Good tests assert external behavior (Profile rows and links written, proposals accepted/refused), never implementation details.
- New tests: direct applier unit tests in `tests/test_apply.py` (each type's apply + refuse paths); the existing per-type accept tests (tranche1 tools, network people/orgs, live activities) pin end-to-end behavior through the flip.
- Regression net: the full suite stays green on every commit A–E.
- Prior art: issue-16 regression tests, applier-mirror structure.

## Out of Scope

- Profile reads (owned by `profile.py`, issue 18).
- New entity types or new link-evidence rules — same rules, new home.
- Remaining candidates C2, C4, C5-remainder, C6.

**Status:** resolved

## Comments

- Implemented in commits b774fec (A shell) → e3287b3 (B tool) → 44c183d (C person/org) → bdce380 (D activity) → 5c7c125 (E flip).
- `src/pre/apply.py`: `APPLIERS` registry + `apply_proposal` dispatch + shared link-evidence helpers; `queue.accept` keeps fetch/status/commit/version-bump only (108 deletions in the flip).
- Grill deviations honored: appliers landed unwired with direct unit tests (never mixed mechanisms mid-flight); version bump stayed in `accept`; profile.py untouched.
- Verification: 7 direct applier tests in tests/test_apply.py; full suite 171 passing on every commit A–E, mypy strict, ruff clean.
