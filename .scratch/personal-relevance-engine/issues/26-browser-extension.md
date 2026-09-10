# 26 — Browser extension (surface + capture lane)

**What to build:** The fourth Outlet (ADR-0004): a toolbar extension doing both jobs per grill — digest reading with one-tap verdicts (same two actions as the web page, reusing the web API, zero new engine semantics) AND visited-product-page capture feeding the Firehose as a lane. The capture half needs its own ingestion rules (dedup against aggregator lanes, per-site consent, noise policy) — specced here, not smuggled.

**Blocked by:** 23 (reuses the web API + auth).

## Grill decisions

- Surface: popup digest + verdicts PLUS page overlay (badge with act/dismiss
  for the visited page when it matches a digest item).
- Auth: Google login launched from the popup through the existing ticket-25
  web flow (no pasted tokens, no second identity path); the login cookie
  authenticates later API calls.
- Capture trigger: automatic (content script on page load) — the tap-free
  default the user chose; manual re-save rides the same endpoint.
- Capture tier: new `browser-capture` lane into `ingest_entries` (fingerprint
  dedup against aggregator lanes comes free); consent + noise rules live in
  `pre/extension.py`, not smuggled into the corpus.
- Packaging: Chrome MV3 source in `extension/`, sideload to test; store
  publish deferred.

**Status:** resolved

Landed: `pre/extension.py` (consent, noise, product-guess rules) + capture,
consent, and overlay API routes + MV3 source in `extension/` +
`docs/extension.md`. Suite green (13 new tests).

- [ ] Toolbar popup: digest list + one-tap act/dismiss against the tenant API
- [ ] Capture lane: visited product pages → candidate Changes (consent-gated)
- [ ] Capture ingestion rules: dedup vs aggregator lanes, noise policy, retention
- [ ] Store listing assets and update channel (Chrome first)
