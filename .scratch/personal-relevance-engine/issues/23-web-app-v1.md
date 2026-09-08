# 23 — Responsive web app v1 (interview + connects + settings)

**What to build:** The first nontechnical Outlet (ADR-0004): a responsive web app growing the existing FastAPI surface, delivered everything-at-once per grill — guided 17-dimension interview onboarding ending at the existing coverage gate; Gmail/Calendar OAuth connects plus takeout-file upload (no CLI); per-dimension threshold settings UI; digest/verdict pages restyled; a tenant-scoped HTTP API underneath that later Outlets reuse. Single-user first (SaaS tenancy arrives in ticket 25).

**Blocked by:** 06 (digest/verdict surface exists); 13 (coverage gate exists).

**Status:** needs-triage

- [ ] Guided interview (17-dimension scaffold as screens, satisfaction sliders) → coverage gate → go-live
- [ ] OAuth connects (calendar, email) + takeout-file upload; full history first, deltas after
- [ ] Threshold settings UI backed by the 34-cell matrix (manual tuning wins)
- [ ] Tenant-scoped HTTP API (digest read, verdict record, interview, settings) with per-user auth
- [ ] Restyled digest/verdict pages on the same API; phone-browser verified
