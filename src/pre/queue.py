"""The confirmation queue: extraction proposals never touch the Profile directly."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import ProposedAssertion, Tool

# Confidence grows with corroborating observations, capped below certainty.
_BASE_CONFIDENCE = 0.5
_OBSERVATION_BONUS = 0.1
_MAX_CONFIDENCE = 0.95


@dataclass
class Proposal:
    entity_type: str
    payload_key: str
    payload: dict[str, Any]
    source_tier: str
    source_ref: str
    confidence: float = _BASE_CONFIDENCE


def row_hash(tier: str, source_ref: str, row_key: str) -> str:
    return hashlib.sha256(f"{tier}|{source_ref}|{row_key}".encode()).hexdigest()


def propose(session: Session, proposal: Proposal) -> ProposedAssertion:
    """Insert or strengthen a pending proposal. Idempotent per (payload_key, tier)."""
    existing = session.scalar(
        select(ProposedAssertion).where(
            ProposedAssertion.payload_key == proposal.payload_key,
            ProposedAssertion.source_tier == proposal.source_tier,
        )
    )
    if existing is not None:
        if existing.status == "pending":
            existing.observations += 1
            existing.confidence = min(
                _MAX_CONFIDENCE,
                existing.confidence + _OBSERVATION_BONUS * (proposal.confidence >= 0.5),
            )
            session.commit()
        return existing

    row = ProposedAssertion(
        entity_type=proposal.entity_type,
        payload_key=proposal.payload_key,
        payload_json=proposal.payload,
        source_tier=proposal.source_tier,
        source_ref=proposal.source_ref,
        row_hash=row_hash(proposal.source_tier, proposal.source_ref, proposal.payload_key),
        confidence=min(_MAX_CONFIDENCE, proposal.confidence),
    )
    session.add(row)
    session.commit()
    return row


def list_pending(session: Session) -> list[ProposedAssertion]:
    return list(
        session.scalars(
            select(ProposedAssertion)
            .where(ProposedAssertion.status == "pending")
            .order_by(ProposedAssertion.confidence.desc(), ProposedAssertion.id)
        ).all()
    )


def accept(session: Session, proposal_id: int) -> Tool | None:
    """Apply a pending proposal to the Profile with full provenance."""
    prop = session.get(ProposedAssertion, proposal_id)
    if prop is None or prop.status != "pending":
        return None

    applied: Tool | None = None
    if prop.entity_type == "tool":
        name = str(prop.payload_json.get("name", "")).strip()
        if not name:
            return None
        applied = session.scalar(select(Tool).where(Tool.name == name))
        if applied is None:
            applied = Tool(name=name)
            session.add(applied)
            session.flush()
        applied.source = f"extraction:{prop.source_tier}"
        applied.confidence = prop.confidence
        applied.last_confirmed_at = datetime.now(UTC)

    if applied is None:
        return None
    prop.status = "accepted"
    prop.decided_at = datetime.now(UTC)
    session.commit()
    return applied


def reject(session: Session, proposal_id: int) -> bool:
    prop = session.get(ProposedAssertion, proposal_id)
    if prop is None or prop.status != "pending":
        return False
    prop.status = "rejected"
    prop.decided_at = datetime.now(UTC)
    session.commit()
    return True


def render_pending(session: Session) -> str:
    lines = ["PENDING PROPOSALS", "=" * 60]
    pending = list_pending(session)
    if not pending:
        lines.append("  (queue empty)")
    for p in pending:
        payload = json.dumps(p.payload_json, ensure_ascii=False)
        lines.append(
            f"  #{p.id} [{p.entity_type}] {payload}  "
            f"tier={p.source_tier} confidence={p.confidence:.2f} obs={p.observations}"
        )
    return "\n".join(lines)
