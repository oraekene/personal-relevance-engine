# Assistant setup: MCP hosts (Claude, Grok, Cursor, opencode, …)

Connect any MCP-speaking assistant to this engine in three steps. Human verification checklist included — the `Verified against one MCP host` ticket box needs an operator with host access.

## 1. Expose the server

The MCP endpoint lives at `https://HOST/mcp` (mounted in the FastAPI app; same
deployment, port, and database as the web surface). It requires HTTPS in
production — OAuth discovery rejects plain HTTP beyond localhost.

## 2. Register the host as a client (operator, once per host)

```pwsh
# Exact commands depend on the host UI; the protocol flow is identical:
# 1. In the host (Claude Settings → Connectors → Add custom connector,
#    Grok → Connectors → New → Custom MCP, Cursor/opencode MCP config),
#    point it at https://HOST/mcp
# 2. The host discovers .well-known/oauth-protected-resource, then
#    .well-known/oauth-authorization-server, then registers via
#    POST https://HOST/oauth/register
# 3. Approve in the browser consent screen with the API token
# 4. The host stores its tokens and calls tools with a bearer credential
```

## 3. Verify (operator checklist for the ticket box)

- [ ] Host lists three tools: `get_digest`, `record_verdict`, `query_profile`
- [ ] "Read my daily digest" returns the same items as the web digest page
- [ ] Dictating "act on item N" records a Verdict visible in `pre verdicts`
- [ ] With assistant answers OFF in Settings, profile questions refuse cleanly
- [ ] Revoked/expired tokens get HTTP 401, never data

## Notes

- Tokens are opaque, hashed at rest, 1-hour access with 30-day rotating refresh.
- Consent master defaults OFF; per-dimension toggles refine it (Settings page).
- Tool outputs tag user data versus third-party content; tools are read-only
  except verdicts, and there is deliberately no bulk-export tool.
