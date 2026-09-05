# Personal Relevance Engine

Filters the daily flood of product features, changes, and improvements down to the few that
matter to one person's Needs and Activities across 17 Life Dimensions — and learns from
their Verdicts.

Spec (`.scratch/personal-relevance-engine/spec.md`), glossary (`CONTEXT.md`),
ADRs (`docs/adr/`), research (`docs/research/`), and the ticket tracker
(`.scratch/personal-relevance-engine/issues/`) all live in this repo. Shared
agent-skill plumbing (`AGENTS.md`, `docs/agents/`) stays at the workspace root.

## Stack (per ADR-0002 and the spec)

- Python 3.11+, SQLAlchemy 2.x models over Postgres (+pgvector later); SQLite for local dev/tests
- Staged-funnel matching: parse → retrieve → judge → calibrate (tickets 03–07)
- MIT-mined connector code per source tier; our own parsers for offline exports (ticket 08+)

## Ticket 01 scope (this commit)

- Goal-hierarchy schema with provenance on every assertion
- The 17 canonical Life Dimensions + sub-dimension interview scaffold (`pre.taxonomy`)
- Network cluster: People, Organizations, relationship context
- Intake flow: interactive or from a YAML file; everything marked `source=interview`
- CLI: `pre intake` / `pre show`

## Development

```pwsh
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\pytest
.venv\Scripts\mypy
.venv\Scripts\ruff check .
```

```pwsh
# Batch intake from a YAML file into a local SQLite profile
.venv\Scripts\pre intake --file tests\fixtures\profile.yaml --db sqlite:///profile.db
.venv\Scripts\pre show --db sqlite:///profile.db
```
