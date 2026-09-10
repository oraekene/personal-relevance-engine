/* Overlay: floating bar on pages matching a digest item. Reports the
 * visit for automatic capture, then asks the server whether the page's
 * host matches anything undecided and offers one-tap verdicts. Never
 * alters the page otherwise; every network failure stays silent. */
"use strict";

(async () => {
  try {
    if (!chrome.runtime?.id) return;
    const got = await chrome.storage.sync.get({ server: "http://127.0.0.1:8787" });
    const base = String(got.server).replace(/\/+$/, "");
    if (location.href.startsWith(base)) return;

    chrome.runtime.sendMessage(
      { type: "page", url: location.href, title: document.title },
      () => chrome.runtime.lastError
    );

    const response = await fetch(base + "/api/overlay?url=" + encodeURIComponent(location.href), {
      credentials: "include",
    });
    if (!response.ok) return;
    const matches = (await response.json()).matches.filter((m) => !m.verdict).slice(0, 3);
    if (!matches.length) return;

    const bar = document.createElement("div");
    bar.style.cssText =
      "position:fixed;right:12px;bottom:12px;z-index:2147483647;background:#fff;" +
      "border:1px solid #888;border-radius:8px;padding:8px 10px;font:13px system-ui;box-shadow:0 2px 8px #0003;";
    for (const match of matches) {
      const row = document.createElement("div");
      row.textContent = `${match.score} — ${match.title} `;
      for (const choice of ["act", "dismiss"]) {
        const button = document.createElement("button");
        button.textContent = choice;
        button.style.marginLeft = "6px";
        button.onclick = async () => {
          await fetch(base + "/api/verdict", {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ item_id: match.item_id, choice }),
          });
          bar.remove();
        };
        row.appendChild(button);
      }
      bar.appendChild(row);
    }
    document.documentElement.appendChild(bar);
  } catch (_error) {
    /* Signed out, offline, or server unreachable: stay invisible. */
  }
})();
