# 16 — Organization proposals can never be accepted (bug)

**What to fix:** `network_extract.py` proposes `entity_type="organization"` contacts (locked by `tests/test_network_extract.py`: org + person proposals asserted), but `queue.accept` has no organization branch — only tool/person/activity, as its docstring admits. Accepting an org proposal returns None and leaves it pending forever, so the Network cluster can never gain Organizations through the confirmation queue, contra ticket 10 ("People and Organizations extracted...").

**Blocked by:** 10 — Network extraction (org proposals exist); 01 — Profile schema (Organization model exists).

**Status:** ready-for-agent

- [ ] `accept` applies organization proposals: get-or-create Organization by name + NetworkLink evidence row (mirror the person path: role/frequency/recency/dimension_code, `source=extraction:<tier>`, confidence, confirmation timestamp)
- [ ] Resolve the dimension_code two-channel gap in the same pass: NetworkLink and coverage read one source (proposal column wins; state the choice in code)
- [ ] Regression test: contacts fixture with organization → import → accept → Organization row + NetworkLink, proposal accepted
- [ ] Full suite green, mypy strict, ruff clean
