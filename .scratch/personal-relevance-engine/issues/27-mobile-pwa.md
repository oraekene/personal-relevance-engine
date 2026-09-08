# 27 — Mobile app via store-wrapped web app (Android first)

**What to build:** The last Outlet (ADR-0004): Android presence via a Trusted Web Activity wrapping the responsive web app — Play Store icon plus web-push notifications for near-zero marginal code. Native Kotlin only on tripwire (widgets, offline-first, or rich push actions demonstrably moving retention). iOS after Android proves out.

**Blocked by:** 23 (wraps the web app); web-push delivery path.

**Status:** needs-triage

- [ ] TWA packaging (icon, Play listing, update channel following web deploys)
- [ ] Web-push notifications for new digest items (per-tenant, quiet hours respected)
- [ ] Tripwire metrics defined (which store-only feature would trigger native, and how measured)
