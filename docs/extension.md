# Browser extension (issue 26)

Fourth outlet: a Chrome toolbar extension (Manifest V3, no dependencies)
doing both jobs — digest reading with one-tap verdicts, and automatic
visited-page capture into a new `browser-capture` corpus lane.

## Install (sideload; store publish deferred)

1. Open `chrome://extensions`, enable Developer mode, Load unpacked → `extension/`.
2. Open the extension options, set the server address (default `http://127.0.0.1:8787`).
3. Tap the toolbar button → Sign in with Google (the ticket-25 web flow; the
   login cookie authenticates later API calls, no tokens are pasted).

## Capture rules

- Consent-gated: capture stays off until the tenant enables it (popup toggle
  or options page); per-host blocklist lives beside the switch.
- Same URL captured once per tenant; a repeat reports `deduped`.
- Capturing an already-known Change (same fingerprint from an aggregator
  lane) only appends this lane to its sources — never a duplicate row.
- Non-page URLs, untitled pages, and the app's own host are never captured.
- Retention follows the corpus policy (`pre prune`).

## Files

`manifest.json` (MV3) · `popup.html/js` (digest + verdicts + toggle) ·
`background.js` (automatic capture) · `content.js` (page overlay with
verdicts for matching items) · `options.html/js` (server + consent).
