# 23 — Responsive web app v1 (interview + connects + settings)

**What to build:** The first nontechnical Outlet (ADR-0004): a responsive web app growing the existing FastAPI surface, delivered everything-at-once per grill — guided 17-dimension interview onboarding ending at the existing coverage gate; Gmail/Calendar OAuth connects plus takeout-file upload (no CLI); per-dimension threshold settings UI; digest/verdict pages restyled; a tenant-scoped HTTP API underneath that later Outlets reuse. Single-user first (SaaS tenancy arrives in ticket 25).

**Blocked by:** 06 (digest/verdict surface exists); 13 (coverage gate exists).

**Status:** resolved

## Comments

- Implemented in commits 398edd3 (1: JSON API + token gate) → 4c07939 (2: Jinja2 + autoescape) → babba95 (3: wizard) → 3ed26cc (4: uploads) → 9f9fa21 (5: presets + grid) → 1361f08 (6: OAuth vault + fetchers + sync-live).
- Review findings, all closed same turn: goal re-application now returns created-vs-reused so summary counts stay honest (plus a recount test); interview JSON missing-satisfaction unified to 400; the three nested error-page closures collapsed into two module helpers using the already-open session (no more double sessions on failure paths); new upload-cap test (413 + tmp cleanup); Gmail 500-recent cap and sync-live partial-failure semantics documented as known limits below.
- Known limits: Gmail backfill caps at 500 recent messages (ids paginate fully; deltas exact) — partial vs the full-history doctrine; `sync-live` commits per service, so a second-service failure leaves the first committed (cron retries nightly); phone-browser verified means responsive markup plus TestClient asserts — on-device check rides with deployment.
- Verification: 58 new route tests across test_api/web/interview/sources/settings/google; full suite 235 passing, mypy strict, ruff clean.

## Commits

- 1. Tenant-scoped JSON API (digest read, verdict record, interview state, settings, ops read-only) behind a single env-configured bearer token; pages keep deployment-proxy auth; TestClient coverage for every route.
- 2. Jinja2 shell + restyled digest/verdict pages served from the same handlers as the JSON API.
- 3. Interview wizard (one Life Dimension step each, progress shown, every step committed, resume where left off) ending at the coverage gate.
- 4. Takeout-file upload feeding the existing canonical import path (full history first, deltas after).
- 5. Settings UI: Quiet/Balanced/Exploratory preset per dimension (primary) mapping to cell pairs, full 34-cell grid as secondary expert menu; verdict auto-tuning untouched, manual cells win.
- 6. Gmail/Calendar OAuth (app registration, consent, encrypted token vault, refresh, background sync) closing v1.

## Decision Document

- Shape: server-rendered Jinja2 pages plus JSON API side by side (grill Q1) — one Python-only stack; TestClient style covers both; no SPA toolchain.
- Auth: single env API token on JSON routes, tenant implied single; full login deferred to ticket 25 (grill Q2).
- Interview: wizard with resume, gate evaluated at each return (grill Q3).
- Connects: upload first (day-one value on proven parsers), OAuth second (grill Q4).
- Settings: presets primary, full grid secondary expert menu (grill Q5, user pick).

## Testing Decisions

- Good tests assert external behavior (onboarding completes to go-live, uploaded file lands in queue, verdict round-trips through API and pages identically), never implementation details.
- New tests: route-level TestClient suites for pages + JSON; wizard resume across sessions; upload-to-queue; preset-to-cell mapping.
- Regression net: full suite stays green on every commit.
- Prior art: web surface tests, cold-start gate tests, matrix tests.

## Out of Scope

- Multi-user login, per-tenant routing, billing — see ticket 25.
- Assistant plugins (they ride this ticket's API) — see ticket 24.
- Native or cross-platform mobile shells — see ticket 27 (PWA wraps this app).

- [ ] Guided interview (17-dimension scaffold as screens, satisfaction sliders) → coverage gate → go-live
- [ ] OAuth connects (calendar, email) + takeout-file upload; full history first, deltas after
- [ ] Threshold settings UI (presets primary, full grid secondary) backed by the 34-cell matrix
- [ ] Tenant-scoped HTTP API (digest read, verdict record, interview, settings) behind one bearer token
- [ ] Restyled digest/verdict pages on the same API; phone-browser verified
