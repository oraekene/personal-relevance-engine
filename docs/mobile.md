# Mobile app (issue 27)

Last outlet: Android presence through a Trusted Web Activity wrapping the
responsive web app, plus Web Push digest alerts with per-tenant quiet hours.

## TWA packaging

1. Serve the app over HTTPS, then set `PRE_ANDROID_PACKAGE` (Play package)
   and `PRE_ASSETLINKS_SHA256` (signing-cert fingerprint). The app serves
   `/.well-known/assetlinks.json` from those values.
2. Export a 512px maskable PNG from `src/pre/icon.svg` (the served SVG keeps
   dev installs working; Play needs the PNG).
3. Fill the placeholders in `twa/twa-manifest.json`, then build:
   `npx @bubblewrap/cli init --manifest https://<host>/manifest.webmanifest`.
4. Upload the bundle to Play; later web deploys flow through with no app update.

## Web push

```pwsh
pre vapid-keygen          # prints PRE_VAPID_PUBLIC_KEY + secret key
$env:PRE_VAPID_PUBLIC_KEY = "<public>"
$env:PRE_VAPID_PRIVATE_KEY = "<secret>"   # never leaves the server
$env:PRE_VAPID_CONTACT = "mailto:you@example.com"
pre notify --registry sqlite:///saas/registry.db   # cron, mornings
```

Phones subscribe from the installed app (subscription terms are per-tenant:
each tenant database holds its own endpoints). Delivery waits out each
tenant's nightly window (default 22:00-07:00 UTC, `/api/push/quiet` edits
it). Dead endpoints are pruned on sight; repeat runs stay silent until new
items assemble.

## Tripwires (when native Kotlin earns its keep)

- Home-screen widget: more than a fifth of active installs ask for one, or a
  widget cohort retains 10+ points better at 30 days.
- Offline-first: push-open failures from missing connectivity pass 5% of opens.
- Rich push actions: verdicts tapped straight from notifications beat
  open-the-app verdicts two to one for a month.

Until a tripwire fires, the web app stays the only client to maintain.
