# SaaS deployment (issue 25)

One engine, many tenants. Each tenant (one person, one profile) gets their
own database file (ADR-0005); a small registry database maps login emails
to database files plus browser sessions.

## Environment

| Variable | Meaning |
| --- | --- |
| `TENANT_REGISTRY_URL` | Registry DB, e.g. `sqlite:///saas/registry.db`. Unset means legacy single-user mode. |
| `TENANT_DBS_DIR` | Directory for auto-provisioned tenant DBs on first Google login. |
| `PRE_GOOGLE_CLIENT_ID` / `PRE_GOOGLE_CLIENT_SECRET` | Google OAuth app (login reuses the ticket-23 client; only the redirect differs). |
| `PRE_GOOGLE_LOGIN_REDIRECT_URL` | Defaults to `http://127.0.0.1:8787/auth/google/callback`. |
| `PRE_API_TOKEN` | Tenant bearer for `/api/*` and for minting assistant credentials. |
| `PRE_OPERATOR_TOKEN` | Operator bearer for `GET /api/ops/tenants` (spend, cap, backup per tenant). Never share with tenants. |
| `PRE_MONTHLY_CAP_CENTS` | Global monthly LLM cap (default 2000). Per-tenant override wins. |

## Operate

```pwsh
# Register a tenant by hand (first Google login also auto-provisions)
pre provision-tenant --email ada@example.com --db-url sqlite:///saas/tenants/ada.db --registry sqlite:///saas/registry.db

# Give one tenant a higher cap without touching the global default
pre provision-tenant --email ada@example.com --db-url sqlite:///saas/tenants/ada.db --registry sqlite:///saas/registry.db --cap-override-cents 5000

# Serve multi-tenant (same binary; the registry URL switches modes)
$env:TENANT_REGISTRY_URL = "sqlite:///saas/registry.db"
pre serve --host 0.0.0.0 --port 8787

# Back up: copy the registry plus every tenant file nightly via cron
pre backup --db sqlite:///saas/registry.db --file backups/registry.db
```

Revoking a login is a row delete (`POST /auth/logout` does it); a stolen
session dies with its row. Billing is deferred to the first paid tier —
spend attribution per tenant is already metered (`/api/ops/tenants`).
