# 24 — Assistant plugin (MCP-first, ChatGPT alongside)

## Problem Statement

Meet users in their assistants: one remote MCP server plus a ChatGPT action pack, over full MCP OAuth with self-issued tokens. (Grill settled every branch; user overrode toward depth twice: full OAuth over token-arg, full profile query over habit-only.)

## Solution

MCP server mounted in the FastAPI app (one deployment): OAuth issuer (authorize + token + refresh + dynamic client registration, opaque self-issued tokens in the Fernet vault), consent-gated full-profile query with provenance labels, habit tools (digest, verdict), and a ChatGPT custom-GPT action pack riding the existing JSON API.

## Commits

- 1. OAuth issuer: authorize/code-exchange/refresh/DCR endpoints, opaque tokens in vault, CSRF nonces; offline tests with faked HTTP.
- 2. Consent model (master switch default off + per-dimension toggles) with settings UI; query tools refuse cleanly when off.
- 3. MCP server: get_digest, record_verdict, query_profile (consent-checked, provenance-labeled, read-only, no bulk export); mounted in app; fake-client conformance tests.
- 4. ChatGPT pack: action instructions + OpenAPI verification against the JSON API; no new code paths.
- 5. Review fixes.

## Decision Document

- Auth: full MCP OAuth, self-issued opaque tokens (grill Q1 user pick, Q2) — no external IdP (wrong audience), no JWT/JWKS (no second consumer).
- Surface: full profile query (grill Q3 user pick) — matrix reads skipped as insufficient, habit-only rejected as insufficient.
- Consent: master switch default off + per-dimension toggles (grill Q4).
- Untrusted content: provenance labels + read-only tools, no bulk export (grill Q5).
- Hosting: mounted in FastAPI, one deployment (grill Q6).
- ChatGPT: built alongside via existing JSON API + instructions pack (grill Q7 user pick), not a separate ticket.

## Testing Decisions

- Good tests assert external behavior (OAuth round-trip offline, consent gating, tool outputs labeled, verdicts land), never implementation details.
- New tests: faked-HTTP OAuth issuer suite; consent on/off matrix; fake-client tool conformance (shapes, auth rejection, no live account); injection-labeled output asserts.
- Regression net: full suite stays green on every commit.
- Prior art: Google OAuth offline suite, API TestClient suites.

## Out of Scope

- Multi-tenant login and per-tenant MCP credentials — see ticket 25 (single token shape grows there).
- Native per-host plugins beyond MCP + ChatGPT action pack.
- Mobile/extension changes.

**Status:** resolved

## Comments

- Implemented in commits 29e648a (1: issuer + vault) → 057984a (2: consent + settings) → 1228c70 (3: tools + mount + host packs) → review fixes (this turn).
- Review findings, all closed: shared render layer (`pre/render.py`, killing a web←auth layering inversion); digest `__all__` completed; top-level imports restored; `dim_`-form parsing unified in `taxonomy.checked_dimension_codes` (unknown codes dropped at write); consent empty-means-empty with legacy absent-flag reading as all (template text now true); metadata drops unusable `none` auth; OAuth authorize flow CSRF-wired (server nonce minted on GET, verified on POST, fresh nonce on error re-render); recent digest items filtered by scope (None-dimension urgent notices always included); unknown-vs-disallowed dimensions distinguished; network follows link dimension on narrowed queries.
- Known limits: Gmail backfill caps at 500 recent (noted in 23); host end-to-end verification needs an operator with Claude/Grok access — connect instructions in `docs/assistant-mcp.md`, checklist there.
- Verification: offline OAuth suite, consent matrix, fake-client tool conformance (registration, shapes, gating, verdict writes, HTTP 401 probe, openapi paths); full suite green, mypy strict, ruff clean.
