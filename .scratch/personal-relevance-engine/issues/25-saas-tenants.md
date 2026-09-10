# 25 — SaaS tenant platform

## Problem Statement

One engine, many Tenants (ADR-0004, ADR-0005): Google signup/login, per-tenant databases with explicit per-request routing, per-tenant spend visibility, and permanent isolation proof. Billing deferred to the first paid tier. (Grill settled every branch.)

## Solution

Central registry database (tenants + opaque browser sessions, instant revocation, no new deps); Google sign-in reusing the ticket-23 OAuth client with tenants auto-provisioned on first login; explicit per-handler tenant session acquisition (auditable security boundary, no middleware magic); engine cache per database URL.

## Commits

- 1. Registry models + tenants module (provision/resolve/sessions) + Google login/logout/callback routes + explicit per-route tenant migration + routing/login tests.
- 2. Cap-override column + operator rollup + MCP/OAuth-issuer tenant binding + spend tests.
- 3. Isolation suite (wrong-tenant fails closed; rows never mix; registry self-protects).
- 4. Deployment story doc + review fixes.

## Decision Document

- Login: Sign in with Google (grill Q1) — reuses tested exchange/userinfo, no passwords, no SMTP; tenants auto-provision; email+password stays fallback.
- Control plane: registry DB with tenants + opaque sessions (grill Q2) — revocation by row delete; JWTs would rebuild the table; shared user tables impossible.
- Routing: explicit per-handler tenant sessions, no middleware (security boundary stays readable; ContextVar/middleware magic rejected).
- Spend: global cap default, per-tenant override, unchanged per-tenant dashboard, env-gated operator rollup (grill Q3).
- Proof: three-class permanent suite on real multi-file DBs (grill Q4).
- Migration: identity, money, proof, docs (grill Q5).
- Web data routes migrate in commit 1; MCP tools and the MCP OAuth issuer bind tenants in commit 2 (token schema change); documented limitation until then.

## Testing Decisions

- Good tests assert external behavior (login lands a tenant, wrong tenant fails closed, spend attributes), never implementation details.
- No live Google account anywhere (faked exchange/userinfo).
- Regression net: full suite stays green on every commit (legacy single-DB mode untouched by default).
- Prior art: Google OAuth offline suite, API TestClient suites.

## Out of Scope

- Billing and payments — first paid tier.
- Email+password or magic-link login — fallback only.

**Status:** resolved

Landed: cap-override tests + operator rollup tests (commit 2), isolation
suite `tests/test_tenant_isolation.py` (commit 3), `docs/deploy-saas.md` plus
`provision-tenant --cap-override-cents` (commit 4). Suite green.
