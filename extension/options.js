/* Options: server URL, capture switch, per-host blocklist. Consent is
 * stored server-side per tenant; this page only edits it. */
"use strict";

async function load() {
  const got = await chrome.storage.sync.get({ server: "http://127.0.0.1:8787" });
  document.getElementById("server").value = got.server;
  try {
    const response = await fetch(got.server + "/api/capture/status", { credentials: "include" });
    if (response.ok) {
      const body = await response.json();
      document.getElementById("capture").checked = !!body.enabled;
      document.getElementById("blocked").value = (body.blocked_hosts || []).join("\n");
    }
  } catch (_error) {
    /* Signed out: server fields stay editable, consent saves after login. */
  }
}

document.getElementById("save").onclick = async () => {
  const server = document.getElementById("server").value.replace(/\/+$/, "");
  await chrome.storage.sync.set({ server });
  const blocked = document
    .getElementById("blocked")
    .value.split("\n")
    .map((h) => h.trim())
    .filter(Boolean);
  const enabled = document.getElementById("capture").checked;
  try {
    await fetch(server + "/api/capture/consent", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled, blocked_hosts: blocked }),
    });
    document.getElementById("saved").textContent = "Saved.";
  } catch (_error) {
    document.getElementById("saved").textContent = "Server unreachable — address kept locally.";
  }
};

load();
