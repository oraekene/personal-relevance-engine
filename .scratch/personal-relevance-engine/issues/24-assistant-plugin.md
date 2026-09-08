# 24 — Assistant plugin (MCP-first)

**What to build:** The second Outlet (ADR-0004): one remote MCP server exposing digest delivery plus verdict capture over tenant-scoped auth, serving the whole MCP camp (Claude, Grok custom connectors, Cursor, opencode et al.) from a single implementation; ChatGPT on its own app/action path second. Coding-agent hosts are distribution, not the nontechnical core. Habit-only scope: read Digest, record Verdict — no chat-over-Profile (that needs profile-query API plus its own privacy design).

**Blocked by:** 23 (needs the web build's HTTP API + per-user auth).

**Status:** needs-triage

- [ ] MCP server: digest-delivery + verdict-capture tools, tenant-scoped credentials
- [ ] Fake-MCP-client conformance tests (tool shapes, auth rejection, no live account)
- [ ] Verified against one MCP host end to end (Claude or Grok)
- [ ] ChatGPT path scoped separately (app/action model, not MCP)
