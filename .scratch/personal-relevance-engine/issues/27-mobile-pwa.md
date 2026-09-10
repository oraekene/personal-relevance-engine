# 27 — Mobile app via store-wrapped web app (Android first)

**What to build:** The last Outlet (ADR-0004): Android presence via a Trusted Web Activity wrapping the responsive web app — Play Store icon plus web-push notifications for near-zero marginal code. Native Kotlin only on tripwire (widgets, offline-first, or rich push actions demonstrably moving retention). iOS after Android proves out.

**Blocked by:** 23 (wraps the web app); web-push delivery path.

## Grill decisions

- Packaging: TWA config in repo (asset-links route, web manifest, Bubblewrap
  file); a full native shell is too heavy for v1 (new language, store
  review, ongoing maintenance for zero proven demand).
- Push: full Web Push with VAPID now (new `pywebpush` dependency accepted);
  per-tenant subscriptions live in each tenant database.
- Quiet hours: per-tenant nightly window (default 22:00-07:00 UTC);
  delivery waits for morning.
- Tripwires: written doc naming the store-only features and the retention
  numbers that trigger native Kotlin work. iOS after Android proves out.

**Status:** resolved

Landed: `pre/push.py` (subscriptions, quiet hours, VAPID sender seam,
notify-once) + PWA routes (manifest, service worker, icon, asset-links) +
push/quiet API + `pre notify`/`pre vapid-keygen` + `twa/` config +
`docs/mobile.md` with tripwires. Suite green (15 new tests).

- [ ] TWA packaging (icon, Play listing, update channel following web deploys)
- [ ] Web-push notifications for new digest items (per-tenant, quiet hours respected)
- [ ] Tripwire metrics defined (which store-only feature would trigger native, and how measured)
