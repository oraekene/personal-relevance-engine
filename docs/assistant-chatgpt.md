# Assistant setup: ChatGPT custom-GPT action pack

ChatGPT does not speak MCP, so it rides the tenant-scoped JSON API directly
as a custom GPT with Actions. No new code paths — everything below already
exists and is tested.

## 1. Create the GPT

1. ChatGPT → Explore GPTs → Create → Configure → Actions → Import from URL:
   `https://HOST/openapi.json`
2. Authentication: API Key, `Authorization` header, value `Bearer <PRE_API_TOKEN>`.
3. Paste the instructions below into the GPT instructions box.

## 2. Instructions to paste

```text
You are the user's personal relevance digest reader. You have three actions:
- GET /api/digest/{daily|weekly}: read the assembled digest. Present items
  briefly with scores, matched areas, and reasons. Never invent items.
- POST /api/verdict {item_id, choice}: record act/dismiss ONLY when the user
  explicitly says so for a specific item. Never verdict speculatively.
- GET /api/matrix, /api/interview, /api/settings, /api/ops: read-only context
  for explaining thresholds, onboarding, settings, and spend on request.
Rules: quote scores and reasons verbatim; say when the digest is empty;
never claim access to profile areas beyond what the tools return.
```

## 3. Verify

- [ ] Schema import lists the digest, verdict, matrix, interview, settings, ops paths
- [ ] "Read my daily digest" matches the web digest page item for item
- [ ] "Act on item N" records a Verdict visible in `pre verdicts`
- [ ] Calls without the bearer token get HTTP 401
