"""Tranche-1 extraction parsers: takeouts, financial, commerce.

Each parser converts one canonical export format into Proposals for the confirmation
queue. v1 contract: the user (or a cleaning step) provides these canonical shapes —

- financial CSV:  date,description,amount         (bank/credit export)
- commerce CSV:   date,item,seller                (order history export)
- takeout JSON:   Google Takeout MyActivity.json  (list of {title,time,services|header})

Revealed Tool usage is the assertion type for this tranche: recurring payments and
repeated service activity propose "user uses <Tool>" with recurrence-scaled confidence.
"""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

from pre.queue import Proposal

# Merchants we can name confidently when the statement text contains them.
_KNOWN_VENDORS = (
    "netflix", "spotify", "adobe", "github", "gitlab", "microsoft", "google", "apple",
    "dropbox", "notion", "figma", "slack", "zoom", "openai", "anthropic", "atlassian",
    "canva", "linear", "vercel", "aws", "amazon web services", "digitalocean", "namecheap",
)

_NOISE = re.compile(r"[^a-z0-9&. ]+")


def _clean_merchant(description: str) -> str:
    text = _NOISE.sub(" ", description.lower())
    for vendor in _KNOWN_VENDORS:
        if vendor in text:
            return vendor.title()
    words = [w for w in text.split() if w.isalpha() and len(w) > 2]
    return " ".join(words[:2]).title() if words else ""


def parse_financial_csv(path: str | Path, source_ref: str | None = None) -> list[Proposal]:
    """Recurring merchant charges -> tool proposals with recurrence-scaled confidence."""
    source_ref = source_ref or str(path)
    counts: Counter[str] = Counter()
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            description = (row.get("description") or row.get("Description") or "").strip()
            if not description:
                continue
            merchant = _clean_merchant(description)
            if merchant:
                counts[merchant] += 1

    proposals: list[Proposal] = []
    for merchant, occurrences in counts.items():
        confidence = min(0.95, 0.5 + 0.15 * (occurrences - 1))
        proposals.append(
            Proposal(
                entity_type="tool",
                payload_key=f"tool:{merchant.lower()}",
                payload={"name": merchant, "observations": occurrences},
                source_tier="financial",
                source_ref=source_ref,
                confidence=confidence,
            )
        )
    return sorted(proposals, key=lambda p: (-p.confidence, p.payload_key))


def parse_commerce_csv(path: str | Path, source_ref: str | None = None) -> list[Proposal]:
    """Order-history rows whose item looks like software/subscriptions -> tool proposals."""
    source_ref = source_ref or str(path)
    counts: Counter[str] = Counter()
    software_hint = re.compile(
        r"\b(software|license|subscription|app|saas|plan|pro|premium|api credits)\b", re.IGNORECASE
    )
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            item = (row.get("item") or row.get("Item") or "").strip()
            seller = (row.get("seller") or row.get("Seller") or "").strip()
            if not item:
                continue
            if not software_hint.search(item):
                continue  # physical goods are phase 2
            name = seller.strip() if seller else _clean_merchant(item)
            if name:
                counts[name] += 1

    return [
        Proposal(
            entity_type="tool",
            payload_key=f"tool:{name.lower()}",
            payload={"name": name, "observations": occurrences},
            source_tier="commerce",
            source_ref=source_ref,
            confidence=min(0.9, 0.45 + 0.15 * (occurrences - 1)),
        )
        for name, occurrences in sorted(counts.items())
    ]


def parse_takeout_activity(path: str | Path, top_n: int = 10) -> list[Proposal]:
    """Google Takeout MyActivity.json -> service-usage proposals for the top services."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise TypeError("expected MyActivity.json to be a list of activity entries")

    counts: Counter[str] = Counter()
    for entry in raw:
        services = entry.get("services") or ([entry["header"]] if entry.get("header") else [])
        for service in services:
            if isinstance(service, str) and service.strip():
                counts[service.strip()] += 1

    total = sum(counts.values()) or 1
    proposals: list[Proposal] = []
    for service, occurrences in counts.most_common(top_n):
        share = occurrences / total
        confidence = min(0.95, 0.4 + share)
        proposals.append(
            Proposal(
                entity_type="tool",
                payload_key=f"tool:{service.lower()}",
                payload={"name": service, "activity_events": occurrences},
                source_tier="takeout",
                source_ref=str(path),
                confidence=confidence,
            )
        )
    return proposals
