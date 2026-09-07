# 20 — Digest owns delivery (C5 remainder)

## Problem Statement

Delivery marking lives in the web layer (`web._digest_html` marks `delivered_at` on serve in live mode) while assembly lives in `digest.py` — and `assemble_digest` still accepts a `shadow` flag it ignores (`_ = shadow`), a leftover from before ticket 06 implemented delivery in web. Meanwhile `render_digest` both renders and writes (marks delivered in live mode), so any future read-only caller would silently file unseen items as seen. (Architecture review C5; grill settled the design.)

## Solution

Digest owns the delivery invariant behind `mark_delivered(session, kind)` (reads the mode table itself, no-ops in shadow); `render_digest` goes pure; the `shadow` parameter is deleted (the mode table is the single source); web calls the owner unconditionally. Two commits, CLI behavior byte-identical throughout.

## Commits

- 1. Delete the `shadow` parameter from `assemble_digest` and fix the one CLI call site (every test already uses the default); suite green.
- 2. Add `mark_delivered` (mode-checked inside), purify `render_digest` (title banner keeps its mode read, marking loop deleted), switch `web._digest_html` to the unconditional call; new invariant tests (render has no side effects even live; shadow serve marks nothing); suite green.

## Decision Document

- Shadow flag: deleted, not honored or aliased (grill Q1) — no CLI flag, test, or doc reaches it; the lone passer recomputes it from the mode table. Deletion fails loud (TypeError) where the old code failed silent.
- Delivery ownership: Digest-owned mark-on-serve (grill Q2) — mark-at-assembly would additionally fix the CLI-wipe oddity but changes visible behavior (terminal reads would count as delivered); leaving marking in web abandons the invariant unification.
- Web mechanics: unconditional `mark_delivered` call, mode check inside (grill Q3) — a mode check in web would re-split the rule.
- Migration: two commits as above (grill Q4) — no mixed mechanisms mid-flight.

## Testing Decisions

- Good tests assert external behavior (digest contents, delivered flags after serve, shadow silence), never implementation details.
- New tests: render purity in live mode; shadow serve marks nothing via the web surface.
- Regression net: web/coldstart/digest/calibration/verdict suites pin current behavior through both commits.
- Prior art: live-mode delivery test, shadow-never-delivers test.

## Out of Scope

- Coldstart gate, spot-check view, and `run_cold_start_cycle` — untouched.
- Mark-at-assembly semantics (see decision) — the CLI-wipe oddity stays as-is.
- Remaining candidates C2, C4, C6.

**Status:** resolved

## Comments

- Implemented in commits 4f873be (shadow deletion) → 2227070 (delivery move).
- `mark_delivered(session, kind)` reads the mode table itself (no-op in shadow); `render_digest` is side-effect-free (it already was — the write lived in `web._digest_html`, a GET with side effects; corrected during implementation); dead `datetime_now_utc` and `_ = utcnow` cruft removed; `web._digest_html` calls the owner unconditionally.
- Observed, not acted: CLI text renderer and web HTML renderer hand-roll the same UNSCORED/STALE flags independently — a future unification candidate, left alone (both formats pinned by tests).
- Verification: 3 new invariant tests (render purity live, mark live/shadow, shadow serve marks nothing); full suite 174 passing, mypy strict, ruff clean.
