/* Popup: digest list with one-tap verdicts, login, capture toggle.
 * Auth is the login cookie from the ticket-25 Google flow (fetch with
 * credentials included); no tokens are ever pasted or stored here. */
"use strict";

const DEFAULT_SERVER = "http://127.0.0.1:8787";
const MAX_POPUP_ITEMS = 20; // keeps the popup readable; the rest wait in the digest

async function server() {
  const got = await chrome.storage.sync.get({ server: DEFAULT_SERVER });
  return String(got.server).replace(/\/+$/, "");
}

async function api(path, options = {}) {
  const base = await server();
  const response = await fetch(base + path, { credentials: "include", ...options });
  if (response.status === 401) throw new Error("login");
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}

function showError(message) {
  document.getElementById("error").textContent = message;
}

async function load() {
  const status = document.getElementById("status");
  try {
    const digest = await api("/api/digest/daily");
    const items = digest.items.filter((item) => !item.verdict);
    status.textContent = items.length ? `${items.length} undecided` : "All decided. Nice.";
    const list = document.getElementById("items");
    list.replaceChildren();
    for (const item of items.slice(0, MAX_POPUP_ITEMS)) {
      const row = document.createElement("li");
      row.textContent = `${item.score} — ${item.entity_label} `;
      for (const choice of ["act", "dismiss"]) {
        const button = document.createElement("button");
        button.textContent = choice;
        button.onclick = async () => {
          await api("/api/verdict", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ item_id: item.id, choice }),
          });
          await load();
        };
        row.appendChild(button);
      }
      list.appendChild(row);
    }
    const capture = await api("/api/capture/status");
    document.getElementById("capture").checked = capture.enabled;
  } catch (error) {
    if (error.message === "login") {
      status.textContent = "Not signed in.";
      document.getElementById("login-row").hidden = false;
    } else {
      showError(String(error.message || error).slice(0, 200));
    }
  }
}

document.getElementById("login").onclick = async () => {
  chrome.tabs.create({ url: (await server()) + "/auth/google/login" });
};

document.getElementById("capture").onchange = async (event) => {
  try {
    await api("/api/capture/consent", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled: event.target.checked }),
    });
  } catch (error) {
    showError(String(error.message || error).slice(0, 200));
    event.target.checked = !event.target.checked;
  }
};

load();
