# 26 — Browser extension (surface + capture lane)

**What to build:** The fourth Outlet (ADR-0004): a toolbar extension doing both jobs per grill — digest reading with one-tap verdicts (same two actions as the web page, reusing the web API, zero new engine semantics) AND visited-product-page capture feeding the Firehose as a lane. The capture half needs its own ingestion rules (dedup against aggregator lanes, per-site consent, noise policy) — specced here, not smuggled.

**Blocked by:** 23 (reuses the web API + auth).

**Status:** needs-triage

- [ ] Toolbar popup: digest list + one-tap act/dismiss against the tenant API
- [ ] Capture lane: visited product pages → candidate Changes (consent-gated)
- [ ] Capture ingestion rules: dedup vs aggregator lanes, noise policy, retention
- [ ] Store listing assets and update channel (Chrome first)
