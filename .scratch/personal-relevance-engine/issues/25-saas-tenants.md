# 25 — SaaS tenant platform

**What to build:** The third Outlet (ADR-0004, ADR-0005): multi-tenant hosting where each Tenant (one person, one Profile) gets its own database. Signup/login, per-tenant database routing (the engine already routes by URL), per-tenant LLM caps and cost attribution on the existing meter, and permanent cross-tenant isolation regression tests (tenant A can never read tenant B — the existential case). Billing deferred to the first paid tier.

**Blocked by:** 23 (grows out of the web app); ADR-0005 (tenancy model decided).

**Status:** needs-triage

- [ ] Signup/login + per-tenant database provisioning and URL routing
- [ ] Per-tenant caps, spend attribution, and ops visibility
- [ ] Isolation tests: cross-tenant reads fail closed, permanently covered
- [ ] Deployment story (hosting, backups per tenant, health per tenant)
