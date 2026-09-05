# Spec: Personal Relevance Engine

Status: ready-for-agent

## Problem Statement

Thousands of web apps, platforms, and digital products ship features, changes, and improvements every day — far more than one person can track. Buried in that flood are Changes that matter to the user's specific Needs and Activities across the 17 dimensions of their life (see `../../CONTEXT.md` — Physical Health, Mental & Emotional Wellbeing, Career, Business, Financial, Social, Relationship, Family, Housing & Home, Community & Civic, Education & Learning, Leisure & Recreation, Environment & Surroundings, Safety & Security, Spirituality & Meaning, Reputational, Autonomy & Time): a pricing change on a Tool they rely on, a deprecation that breaks a Workflow, a new product that solves a long-standing Need. Today there is no system that knows the user's life in enough structure to filter the flood down to what is actually relevant — so relevant Changes are missed entirely, or discovered late by accident.

## Solution

A Personal Relevance Engine that maintains a complete structured Profile of the user (the "database of me"), ingests Changes from both a Watchlist of products the user already uses and curated Firehoses of product announcements, and matches them through a staged funnel — parse, retrieve, LLM-judge, calibrate — to produce two ranked Digests: a precise daily digest (≤5 items) and an exploratory weekly digest (≤20 items). Every Digest item records a Verdict (act / dismiss), and Verdicts recalibrate the relevance thresholds. The user reads the Digest and records Verdicts from any device.

## User Stories

1. As the user, I want a structured interview to build the skeleton of my Profile, so that the system has an accurate starting model of my life without waiting for data extraction.
2. As the user, I want my Profile organized as Life Dimension → Goals → Needs → Activities → Tasks → Tools plus a Network cluster (People, Organizations, relationship context), so that relevance is judged against why and how I actually live and who I live with.
3. As the user, I want each Life Dimension to carry a satisfaction score, so that low-satisfaction dimensions surface as high-relevance areas.
4. As the user, I want every Profile assertion to carry its source, confidence, and last-confirmed date, so that I can trust or challenge what the system believes about me.
5. As the user, I want stale Profile assertions flagged, so that the system never silently matches against an outdated picture of my life.
6. As the user, I want extraction to propose Profile updates and to confirm or reject them, so that the Profile stays calibrated without me maintaining it by hand.
7. As the user, I want batch exports from all ten source tiers (takeouts, financial, commerce, comms, calendar, productivity/notes, social/content, device exhaust, health/location, work systems) to feed my Profile, so that it reflects my revealed behavior, not just what I said in an interview.
8. As the user, I want connecting any source to pull its entire available history on first connect (then deltas), so that the Profile is complete, not born today.
9. As the user, I want live connectors for fast-changing sources (calendar, email), so that the Profile is fresh where freshness matters.
10. As the user, I want my Network extracted from comms, calendar, contacts, and social sources, so that relationship- and family-dimension matching knows who I interact with.
11. As the user, I want every Tool in my Profile automatically on the Watchlist, so that changes to products I depend on are always monitored.
12. As the user, I want the Watchlist monitored for deprecations, pricing changes, and security-relevant Changes, so that I'm never surprised by a Tool I rely on.
13. As the user, I want curated Firehoses (changelog aggregators, launch platforms, app-store update feeds) ingested in bulk, so that new products that match my Needs can be discovered even when I've never heard of them.
14. As the user, I want each incoming Change parsed into a structured record (product, what changed, who it affects, pricing/deprecation/security flags), so that matching reasons about substance rather than marketing prose.
15. As the user, I want embedding retrieval to shortlist candidate Profile entities for each Change, so that expensive judging only runs on plausible matches.
16. As the user, I want an LLM judge to score shortlisted Change × Profile pairs with full context and give reasons, so that I can see *why* something was judged relevant.
17. As the user, I want a daily Digest of at most 5 items run at a precise threshold, so that my daily attention is spent only on near-certain matches.
18. As the user, I want a weekly Digest of at most 20 items run at an exploratory threshold, so that long shots and new-product discovery have a place to surface.
19. As the user, I want each Digest item to show the matched Goal/Need/Activity and the judge's reasoning, so that I can Verdict quickly and trust the ranking.
20. As the user, I want to record a Verdict (act / dismiss) on every Digest item with one tap, so that the calibration signal is complete and low-friction.
21. As the user, I want to read the Digest and record Verdicts from my phone or any device, so that the habit fits my life instead of anchoring me to one machine.
22. As the user, I want relevance thresholds maintained as a matrix of digest type × Life Dimension, so that e.g. business can run exploratory while family runs precise.
23. As the user, I want threshold cells to initialize at digest defaults and differentiate through my Verdicts, so that the system personalizes per dimension without me configuring 34 knobs on day one.
24. As the user, I want to hand-override any threshold cell, so that I keep final control over what reaches me.
25. As the user, I want Verdicts recorded against the Profile version that produced the Digest item, so that recalibration stays meaningful as my life changes.
26. As the user, I want judge API calls cost-metered with a monthly cap and an alert, so that the bill never surprises me.
27. As the user, I want a shadow-mode cold start — silent corpus collection, then shadow judging with spot-checks — before the first real Digest, so that the system earns trust before it claims my attention.
28. As the user, I want urgent Watchlist notices (deprecation/security) surfaced during cold start labeled UNSCORED, so that a genuinely time-sensitive Change isn't withheld just because calibration isn't done.
29. As the user, I want a Profile coverage check to gate go-live, so that the Digest doesn't launch against a half-built picture of me.

## Implementation Decisions

- **Modules**: (1) Extraction workers (batch-export parsers per source tier + live connectors for calendar/email), (2) Watchlist/Firehose ingestion workers, (3) Change parser, (4) Matching pipeline (retrieval → judge → calibration), (5) Digest assembler + threshold matrix, (6) Digest/verdict surface (dashboard + push links), (7) Ops (cost meter, provider health, backups).
- **Profile schema**: Life Dimension → Goals → Needs → Activities → Tasks → Tools, plus the Network cluster (People, Organizations, relationship context). Every assertion carries `source`, `confidence`, `last_confirmed_at`; staleness flags follow the param-expiry pattern from the user's existing radar system. Each Life Dimension carries a satisfaction score (low satisfaction raises matching signal). The 17-dimension taxonomy and sub-dimension interview scaffold are in `../../docs/research/life-dimension-taxonomies.md`.
- **Change record**: structured parse of each Change — product, change type (feature / improvement / deprecation / pricing / policy / security), affected users, and flags.
- **Matching**: staged funnel per ADR-0002. Local embedding model computes vectors; Postgres + pgvector stores them; LLM judge API scores shortlists with full Profile context and returns a score plus reasoning.
- **Threshold matrix**: 2 digests × 17 Life Dimensions = 34 independently tuned cells, initialized at digest defaults, recalibrated from Verdicts, hand-overridable.
- **Verdicts**: logged per Digest item, recorded against the Profile version in force when the item was judged.
- **Connectors**: connector code is mined from MIT-licensed codebases per source tier — LlamaIndex LlamaHub loaders (comms/notes/social), Meltano Singer taps (financial/commerce/work), community MCP servers as auth-pattern reference — plus our own parsers for offline exports (Takeout MBOX/JSON, bank CSVs, order exports) which no project covers. n8n and Nango are excluded (fair-code licenses). Full research in `../../docs/research/open-source-connector-ecosystems.md`. Every source pulls its full available history on first connect, then deltas.
- **Storage/hosting**: fully cloud-hosted per ADR-0001 — Profile DB, corpus, workers, and digest surface all reachable from any device; no minimization tiers.
- **Cold start**: ~4 weeks. Weeks 1–2: interview skeleton + Watchlist assembly + silent Firehose ingestion. Weeks 3–4: extraction populates the Profile; judge runs in shadow mode with spot-checks. Go-live gated on a Profile coverage check. Urgent Watchlist notices may surface during cold start labeled UNSCORED.
- **Cost doctrine**: every judge call cost-metered; monthly cap with pre-invoice alert (mirrors the provider-cost metering in the user's existing systems).
- **Proportionality doctrine**: one Postgres (+pgvector), Python workers, cron/APScheduler. No separate vector DB, no queue infrastructure, no microservices.

## Testing Decisions

- Good tests assert **external behavior** (what lands in the Digest, what a Verdict changes), never implementation details (prompt wording, embedding internals).
- Three seams, per user confirmation:
  - **Source seam**: fixture exports and fixture Firehose/Watchlist payloads drive ingestion and extraction — no live network in tests.
  - **Judge seam**: the LLM judge sits behind an interface; tests substitute a scripted judge.
  - **Digest/verdict seam**: the dashboard/push-link API is the behavioral surface — end-to-end tests assert digest contents, ranking, and the calibration effects of Verdicts here.
- Prior art: none in this workspace (greenfield); the user's radar system in `working-system-architecture-v4.md` is the doctrinal reference (calibration tables, provider health, cold-start discipline).

## Out of Scope

- Hardware/physical products (firmware updates, recalls, new models) — deferred to phase 2.
- Urgency-routed real-time alerting as a primary mode — rejected in favor of Digests; UNSCORED urgent notices during cold start are the only exception.
- Autonomous action-taking (auto-adopting, auto-purchasing) — the system's action loop ends at a logged human Verdict.
- Multi-user support — the Profile model assumes exactly one user.
- Live connectors beyond calendar and email in v1.

## Further Notes

- Glossary: see `../../CONTEXT.md` (Change, Digest, Verdict, Watchlist, Firehose, Profile, Life Dimension, Goal, Need, Activity, Workflow, Task, Tool, Network).
- ADRs: `../../docs/adr/0001-cloud-hosted-profile-over-local-first.md`, `../../docs/adr/0002-staged-funnel-matching.md`.
- Research: `../../docs/research/life-dimension-taxonomies.md` (17 dimensions, sources), `../../docs/research/open-source-connector-ecosystems.md` (connector codebases, licenses, fact-checks).
- Design details deliberately left to implementation: Firehose dedup, corpus retention, judge prompt design, the exact coverage-check definition, Watchlist seeding mechanics.
