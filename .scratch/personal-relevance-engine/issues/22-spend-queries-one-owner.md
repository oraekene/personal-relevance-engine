# 22 — Spend queries under one owner (C6 remainder)

## Problem Statement

`spend_by_month` lived in `ops.py` while `cost_meter.py` owned every other spend query (tables, month-to-date, cap checks). One spend owner, no behavior change. (Architecture review C6, narrowed by grill: the funnel half of C6 did not survive scrutiny — see below.)

## Solution

Move `spend_by_month` verbatim into `cost_meter.py`; `ops.py` and tests import it from there; drop it from `ops.__all__` with the now-unused model import.

## Commits

- Single commit (pure relocation + import updates).

## Decision Document

- Grill Q1 scoped C6 to this move only. Verified before acting: `scoring.judge_change` already orchestrates shortlist→judge→store with cli plus ~10 test call sites reusing it (deleting it would scatter, not concentrate — the report's claim was wrong); budget enforcement sits solely inside `LLMJudge.score`, the only spending path (moving it shared would wrongly block free offline judging at cap); the ops-split tripwire hasn't fired (two smooth `ops.py` changes since parking).
- `ops.py` split stays parked; single-subclass-tripwire and C4-f derivations unchanged.

## Testing Decisions

- No new tests: the move is byte-identical and the existing per-period test moves with it.
- Regression net: full suite green, mypy strict, ruff clean.

## Out of Scope

- Funnel restructuring of any kind.
- `ops.py` decomposition.
- Remaining candidate C4 (parked as ADR-0003).

**Status:** resolved

## Comments

- Implemented in commit c4e7647.
- Verification: full suite 175 passing, mypy strict, ruff clean.
