# 01 — Profile schema + interview intake

**What to build:** The user sits down for a structured intake interview and walks out with a populated Profile skeleton: the 17 research-backed Life Dimensions → Goals → Needs → Activities → Tasks → Tools, plus the Network cluster (People, Organizations, relationship context). Every assertion carries source, confidence, and last-confirmed-at; every Life Dimension carries a satisfaction score. The interview walks a sub-dimension scaffold so no area of life is silently skipped.

**Blocked by:** None — can start immediately.

**Status:** resolved

- [x] Schema implements the full goal hierarchy with provenance fields on every assertion (per CONTEXT.md vocabulary)
- [x] All 17 canonical Life Dimensions present, using the sub-dimension scaffold from `research/life-dimension-taxonomies.md`
- [x] Network cluster modeled: People, Organizations, relationship context
- [x] Structured intake flow captures Goals, Needs (with horizon), Activities (with cadence), Tasks, current Tools, and per-dimension satisfaction scores
- [x] Interview-sourced assertions marked source=interview with initial confidence and confirmation timestamp
- [x] The populated skeleton is viewable and correctable by the user

## Comments

- Implemented in `personal-relevance-engine/` repo (github.com/oraekene/personal-relevance-engine), commit 3d309e7.
- Intake is batch-YAML first (`pre intake --file`); interactive terminal walking deferred — the agent-assisted interview path uses YAML documents anyway.
- Correction path for now is re-running intake against a fresh DB; an `edit` command can come later if needed.
- Verification: 19 tests pass, mypy strict clean, ruff clean, CLI smoke-tested end-to-end.
