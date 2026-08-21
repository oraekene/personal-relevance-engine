"""The Change corpus: parse, classify, fingerprint, and deduplicate product news."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import Change

CHANGE_TYPES = ("feature", "improvement", "deprecation", "pricing", "policy", "security")

_DEPRECATION = re.compile(r"\b(deprecat|sunset|end[- ]of[- ]life|eol|discontinu|remov)\w*", re.IGNORECASE)
_PRICING = re.compile(r"\b(pric|pricing|price|billing|plan cost|subscription fee|rate change)\w*", re.IGNORECASE)
_SECURITY = re.compile(r"\b(security|vulnerab|CVE-\d+|breach|exploit|patch)\w*", re.IGNORECASE)
_POLICY = re.compile(r"\b(policy|terms of service|tos|privacy policy|compliance|gdpr)\w*", re.IGNORECASE)
_IMPROVEMENT = re.compile(r"\b(improv|faster|performance|optimiz|refin|enhanc|better)\w*", re.IGNORECASE)


@dataclass(frozen=True)
class FirehoseEntry:
    product_name: str
    title: str
    url: str | None = None
    published_at: datetime | None = None
    summary: str = ""


@dataclass
class IngestResult:
    created: int = 0
    deduped: int = 0
    fingerprints: list[str] = field(default_factory=list)


def classify(title: str, summary: str = "") -> dict[str, Any]:
    """Keyword heuristics over title+summary -> change_type and flags.

    Order matters: security > deprecation > pricing > policy > improvement > feature.
    """
    text = f"{title} {summary}"
    if _SECURITY.search(text):
        return {"change_type": "security", "is_security": True}
    if _DEPRECATION.search(text):
        return {"change_type": "deprecation", "is_deprecation": True}
    if _PRICING.search(text):
        return {"change_type": "pricing", "is_pricing": True}
    if _POLICY.search(text):
        return {"change_type": "policy"}
    if _IMPROVEMENT.search(text):
        return {"change_type": "improvement"}
    return {"change_type": "feature"}


def fingerprint_for(product_name: str, title: str) -> str:
    """Near-dup hash: same change reported by different lanes collapses to one corpus row."""
    normalized = re.sub(r"[^a-z0-9]+", " ", f"{product_name} {title}".lower()).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def ingest_entries(
    session: Session, entries: list[FirehoseEntry], source_name: str
) -> IngestResult:
    """Insert new Changes; append this lane to sources for near-duplicates."""
    result = IngestResult()
    for entry in entries:
        fp = fingerprint_for(entry.product_name, entry.title)
        existing = session.scalar(select(Change).where(Change.fingerprint == fp))
        if existing is not None:
            if not any(s.get("source") == source_name for s in existing.sources_json):
                existing.sources_json = [*existing.sources_json, {"source": source_name,
                                                                  "url": entry.url or ""}]
            result.deduped += 1
            continue

        classification = classify(entry.title, entry.summary)
        session.add(
            Change(
                product_name=entry.product_name,
                title=entry.title,
                url=entry.url,
                published_at=entry.published_at,
                fingerprint=fp,
                sources_json=[{"source": source_name, "url": entry.url or ""}],
                **classification,
            )
        )
        result.created += 1
        result.fingerprints.append(fp)
    session.commit()
    return result
