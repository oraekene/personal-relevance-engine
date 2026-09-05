# 06 — Verdict capture + anywhere access

**What to build:** Every Digest item accepts a one-tap Verdict (act / dismiss), logged against the Profile version that produced the item. The digest surface is cloud-deployed and reachable from phone or any device (per ADR-0001), with push links landing on the same surface.

**Blocked by:** 05 — Digest assembly + 34-cell threshold matrix.

**Status:** resolved

- [x] One-tap Verdict recording on every Digest item (act / dismiss)
- [x] Verdicts are logged against the Profile version in force when the item was judged
- [x] Digest surface is cloud-hosted and reachable from any device
- [x] Push links deliver the user to the Digest surface

## Comments

- Implemented in commit 4b3580a (github.com/oraekene/personal-relevance-engine).
- Profile version: monotonic counter (SystemFlag) bumped on intake application and every queue acceptance; DigestItems stamp the version in force at assembly; `VerdictLog` records each verdict against it — permanent audit trail with channel tracking ('cli' | 'web' | 'push').
- Exactly one verdict per item, enforced and tested; invalid choices and unknown items rejected.
- Web surface: minimal FastAPI app (`pre serve`, `[serve]` extra) rendering digests as one-tap act/dismiss buttons; reachable from any device once cloud-hosted behind mesh/auth proxy (auth is deployment's job). Live mode marks items delivered when served; shadow mode never does.
- Push links: stable URL shape via `push_link(base_url, item_id)` for future push channels.
- In-memory SQLite now uses StaticPool so web worker threads share the test DB connection.
- Verification: 7 web tests + 10 verdict tests; full suite 138 passing, mypy strict clean, ruff clean.
