# Research: Open-Source Connector Ecosystems

Findings for the Personal Relevance Engine's connector infrastructure decision. All claims verified against primary sources (READMEs, license files, docs) on 2026-08-19; corrected rows re-verified 2026-09-05.

## Comparison

| Project | License (verified) | Connectors | Language | Orientation | Reusable in Python project? |
|---|---|---|---|---|---|
| Airbyte | ELv2 for connectors + almost all public repos, MIT for airbyte-protocol only (docs/community/licenses) | 600+ | Java/Python CDK | Extraction (ELT) | Partially — connectors depend on the Airbyte CDK, and ELv2 forbids lifting |
| Meltano / Singer taps | Meltano engine MIT; Singer SDK Apache-2.0; per-tap licenses vary (e.g. tap-github Apache-2.0, tap-gmail MIT) | 600+ taps via hub.meltano.com | Python | Extraction (ELT) | High — each tap is a standalone pip package |
| LlamaIndex / LlamaHub | MIT (llama-hub repo archived into the llama-index monorepo, still MIT) | 300+ integration packages | Python | Extraction (data loaders) | High — per-integration pip packages (llama-index-readers-*); the old llama-hub package is frozen at 0.0.79.post1 |
| Composio | MIT SDK | 1000+ toolkits | Python/TS SDKs | Action (agent tool-calling) | Low — SDK wraps hosted execution (API key, hosted MCP endpoints) |
| Nango | Elastic License (fair-code) | 900+ APIs | TypeScript | Sync + action | No — copying is license-restricted |
| n8n | Sustainable Use License (fair-code) | 1500+ integrations | TypeScript | Automation | No — copying is license-restricted |
| Activepieces | MIT core, Commercial license for ee dirs (open-core) | 280+ pieces, also published as MCP servers | TypeScript | Automation/actions | Medium — clean standalone piece modules; TS, not Python; check per-file ee headers |
| MCP servers | Apache-2.0 / MIT (reference repo; community servers vary) | Registry at registry.modelcontextprotocol.io | Python/TS | Action tools | Medium — read-only Python servers are pip-installable |
| Pipedream | Source Available License v1.0 since Jan 2023 — NOT MIT (package.json "MIT" string is stale) | 1000+ apps | Node.js | Automation/actions | No — source-available with competing-SaaS exclusion; also Node, not Python |
| Huginn | MIT | ~100 agents | Ruby | Automation | Low |

## The user's AI-bot claims — fact-check

- **Vercel eve.dev — REAL** (https://eve.dev, github.com/vercel/eve): a "durable AI agent framework". But it does **not** embed hundreds of connectors. Its `connections/` layer is MCP-client wrappers (`defineMcpClientConnection({ url: "https://mcp.linear.app/mcp" })`) — eve *delegates* to the MCP ecosystem. Connector code lives in MCP servers, not in eve.
- **Cloudflare Agents — REAL** (developers.cloudflare.com/agents): durable agent runtime (Agents SDK) whose tool story is MCP tools, browser, sandbox, AI Search, payments. No open-source "hundreds of connectors" repo; again MCP delegation.
- **xAI / Grok — NO open-source library, but a hosted catalog exists** (github.com/xai-org): grok-1 (model weights, Apache-2.0), x-algorithm, grok-build (coding agent TUI), xai-sdk-python, grok-prompts, plugin-marketplace. Still **no open-source connector library** — but Grok has a hosted Connectors catalog (built-in OAuth connectors for Gmail/Drive/Outlook/Teams/SharePoint/Salesforce plus BYO MCP; docs.x.ai/grok/connectors). Hosted execution only, not reusable connector code — the recommendation below is unchanged.

**Pattern confirmed:** every "AI bot with hundreds of connectors" gets them via MCP. The reusable open-source connector code lives in the MCP registry, LlamaHub, Meltano taps, and Activepieces — not in the bot frameworks.

## License gotchas

- n8n and Nango are **fair-code / Elastic** — source-available but you cannot lift connector code into your own project.
- Airbyte is ELv2 for connectors and almost all public repos (MIT for airbyte-protocol only) — do not lift connector code.
- Pipedream's main repo is Source Available v1.0 (Jan 2023), not MIT — do not lift component code.
- Composio's SDK is MIT but the 1000+ toolkits execute on their hosted infra — copying SDK code does not give you connectors.

## Recommendation for our ten source tiers

Mine three MIT-licensed Python-native codebases, in this order:

1. **LlamaIndex LlamaHub loaders** — Python data loaders for Gmail, Drive, Notion, Slack, Discord, etc.; designed to be imported standalone (`pip install llama-index-readers-*`). Best fit for comms/notes/social tiers.
2. **Meltano Singer taps** — standalone Python extractors for business/financial/commerce SaaS (Stripe, Shopify, QuickBooks, banks via Plaid-style taps). Best fit for financial/commerce/work tiers.
3. **Community MCP servers** (registry.modelcontextprotocol.io) — Python read-only servers (gmail, calendar, notion) as last-resort reference code for auth patterns.

Build ourselves regardless: parsers for platform exports (Google Takeout MBOX/JSON, Apple export, bank CSVs, Amazon/order exports) — no project covers offline export parsing; that's our proprietary parsing layer. Skip n8n/Nango/Airbyte-connectors/Pipedream (license), treat Composio as a hosted-execution option to revisit later if ever.
