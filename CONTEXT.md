# Personal Relevance Engine

A system that filters the daily flood of product features, changes, and improvements down to the few that matter to one person's needs and activities across every dimension of their life — and learns from their verdicts.

## Language

**Change**:
A discrete unit of product news — a new feature, modification, improvement, deprecation, pricing/policy change, or recall — released by a product maker.
_Avoid_: update (overloaded), news, release

**Digest**:
The system's primary output — a periodic, ranked list of Changes judged relevant to the user's Needs and Activities, presented for a Verdict.
_Avoid_: feed, newsletter, report

**Verdict**:
The user's logged decision on a Digest item (act / dismiss), which retrains relevance scoring. Every Digest item collects exactly one.
_Avoid_: feedback, label

**Watchlist**:
The set of products the user already uses or owns, monitored for Changes at high priority. Every Change to a Watchlist product is relevance-candidate by default.
_Avoid_: subscriptions, followed products

**Profile**:
The complete structured model of the user, organized as a goal hierarchy: Life Dimension → Goals → Needs → Activities → Tasks → Tools. Built from a structured interview skeleton plus full data extraction from source systems; the matching engine's primary input.
_Avoid_: user model, persona

**Life Dimension**:
A top-level area of the user's life. Canonical set of 17: Physical Health, Mental & Emotional Wellbeing, Career, Business, Financial, Social, Relationship, Family, Housing & Home, Community & Civic, Education & Learning, Leisure & Recreation, Environment & Surroundings, Safety & Security, Spirituality & Meaning, Reputational, Autonomy & Time. Research-derived taxonomy in `docs/research/life-dimension-taxonomies.md`. Each Dimension carries a satisfaction score; the root of each branch of the Profile hierarchy.
_Avoid_: domain (overloaded), category

**Goal**:
A long-term outcome the user wants within a Life Dimension. Owns Needs.
_Avoid_: objective, aspiration

**Need**:
A requirement — immediate or long-term — that serves a Goal. Carries a pain level and an openness-to-change.
_Avoid_: want, desire

**Activity**:
A recurring thing the user does (daily, weekly, monthly, or long-cycle) in service of Needs. Decomposes into Tasks. Carries its current Tools.
_Avoid_: habit, routine

**Workflow**:
A chain of Activities linked toward one outcome (the user's "complex workflows").
_Avoid_: process, pipeline

**Task**:
A single step inside an Activity, executed with one or more Tools.
_Avoid_: step, action

**Tool**:
A product or service the user currently employs to execute Tasks. Every Tool in the Profile is automatically on the Watchlist.
_Avoid_: app, solution

**Network**:
The cluster of People, Organizations, and the user's relationship context to each, extracted mainly from comms, calendar, contacts, and social sources. Sits alongside the goal hierarchy in the Profile.
_Avoid_: contacts (too flat), social graph

**Firehose**:
A curated broad source of Change announcements (changelog aggregators, launch platforms, app-store update feeds), ingested in bulk and filtered hard against the Profile.
_Avoid_: stream, crawl

## Scope decisions

- v1 covers software only. Hardware/physical products (firmware, recalls, new models) are deferred to phase 2.
- v1 Profile extraction covers all ten source tiers (takeouts, financial, commerce, comms, calendar, productivity/notes, social/content, device exhaust, health/location, work systems). Access model: batch exports + a few live connectors (calendar, email).
- Profile storage: fully cloud-hosted, no minimization tiers — optimized for anywhere-access and maximum judge quality. Accepted consequence: the complete joined Profile (financial, health, location, comms) rests with third parties under their default protections.
- Relevance posture: a threshold matrix of digest (daily/weekly) × Life Dimension. Cells initialize at digest defaults (daily = precise, weekly = exploratory) and differentiate via Verdicts; any cell is hand-overridable.
- Connector backfill: connecting any source pulls its entire available history on first connect, then deltas thereafter.
- Connector strategy: mine MIT-licensed codebases per source tier (LlamaIndex LlamaHub loaders, Meltano Singer taps, community MCP servers as reference) plus our own parsers for offline exports. No fair-code codebases (n8n, Nango excluded). Research in `docs/research/open-source-connector-ecosystems.md`.
