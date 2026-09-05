# 16 — Organization proposals can never be accepted (bug)

**What to fix:** `network_extract.py` proposes `entity_type="organization"` contacts (locked by `tests/test_network_extract.py`: org + person proposals asserted), but `queue.accept` has no organization branch — only tool/person/activity, as its docstring admits. Accepting an org proposal returns None and leaves it pending forever, so the Network cluster can never gain Organizations through the confirmation queue, contra ticket 10 ("People and Organizations extracted...").

**Blocked by:** 10 — Network extraction (org proposals exist); 01 — Profile schema (Organization model exists).

**Status:** resolved

- [x] `accept` applies organization proposals: get-or-create Organization by name + NetworkLink evidence row (mirror the person path: role/frequency/recency/dimension_code, `source=extraction:<tier>`, confidence, confirmation timestamp)
- [x] Resolve the dimension_code two-channel gap in the same pass: NetworkLink and coverage read one source (proposal column wins; state the choice in code)
- [x] Regression test: contacts fixture with organization → import → accept → Organization row + NetworkLink, proposal accepted
- [x] Full suite green, mypy strict, ruff clean

## Comments

- Implemented in commit e0d12b8.
- Organization branch mirrors the person branch (get-or-create + per-tier NetworkLink); full unification stays with C3 (breadcrumb in code).
- Single source: `_link_dimension_code` (column wins, payload fallback) + `_should_write_link` shared by both branches; `enrich_person_proposal` now propagates the hint to the proposal column — without that write, coverage (column reader) stays blind to comms/social/contacts hints, so the write completes the merge rather than creeping scope. The link-condition extension is currently unreachable in prod (no proposer emits column-only person/org hints from non-Network tiers; tranche3's dimensioned proposals are tools) and is locked by a health-tier test.
- Verification: 4 new tests in tests/test_network_extract.py; full suite 164 passing, mypy strict, ruff clean.
