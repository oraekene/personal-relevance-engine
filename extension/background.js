/* Background: automatic capture. The content script reports each loaded
 * page; this worker POSTs it when the tenant enabled capture and the host
 * is not blocked. The server re-checks consent and dedupes, so a lost
 * status cache only ever risks a refused request, never a leak. */
"use strict";

const DEFAULT_SERVER = "http://127.0.0.1:8787";
let statusCache = null; // {at, enabled, blocked}

async function server() {
  const got = await chrome.storage.sync.get({ server: DEFAULT_SERVER });
  return String(got.server).replace(/\/+$/, "");
}

async function captureStatus(base) {
  const now = Date.now();
  if (statusCache && now - statusCache.at < 5 * 60 * 1000) return statusCache;
  const response = await fetch(base + "/api/capture/status", { credentials: "include" });
  if (!response.ok) return { enabled: false, blocked: [] };
  const body = await response.json();
  statusCache = { at: now, enabled: !!body.enabled, blocked: body.blocked_hosts || [] };
  return statusCache;
}

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (!message || message.type !== "page") return;
  (async () => {
    try {
      const base = await server();
      const host = new URL(message.url).hostname.toLowerCase();
      if (host === new URL(base).hostname.toLowerCase()) return; // never capture the app itself
      const status = await captureStatus(base);
      if (!status.enabled || status.blocked.includes(host)) return;
      await fetch(base + "/api/capture", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: message.url, title: message.title }),
      });
    } catch (_error) {
      /* Offline or signed out: the next page load retries. */
    }
    reply(true);
  })();
  return true;
});
