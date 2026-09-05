"""The confirmation queue: extraction proposals never touch the Profile directly."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.apply import link_dimension_code, should_write_link
from pre.models import (
    Activity,
    Need,
    NetworkLink,
    Organization,
    Person,
    ProposedAssertion,
    Tool,
)

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
    dimension_code: str | None = None


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
        dimension_code=proposal.dimension_code,
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


def accept(session: Session, proposal_id: int, decided_via: str = "manual") -> Any:
    """Apply a pending proposal to the Profile with full provenance.

    Supported entity types: 'tool' (get-or-create), 'person' (Network),
    'organization' (Network), and 'activity' (requires payload['need_id']
    pointing at an existing Need).
    """
    prop = session.get(ProposedAssertion, proposal_id)
    if prop is None or prop.status != "pending":
        return None

    applied: Any = None
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
    elif prop.entity_type == "person":
        name = str(prop.payload_json.get("name", "")).strip()
        if not name:
            return None
        applied = session.scalar(select(Person).where(Person.display_name == name))
        if applied is None:
            applied = Person(display_name=name)
            session.add(applied)
            session.flush()
        applied.source = f"extraction:{prop.source_tier}"
        applied.confidence = prop.confidence
        applied.last_confirmed_at = datetime.now(UTC)
        # Relationship context (ticket 10): accepting a person also writes their
        # NetworkLink when the proposal carries relationship fields.
        if should_write_link(prop):
            existing_link = session.scalar(
                select(NetworkLink).where(
                    NetworkLink.person_id == applied.id,
                    NetworkLink.source == f"extraction:{prop.source_tier}",
                )
            )
            if existing_link is None:
                session.add(
                    NetworkLink(
                        person_id=applied.id,
                        role=prop.payload_json.get("role"),
                        frequency=prop.payload_json.get("frequency"),
                        recency=prop.payload_json.get("recency"),
                        dimension_code=link_dimension_code(prop),
                        source=f"extraction:{prop.source_tier}",
                        confidence=prop.confidence,
                    )
                )
    elif prop.entity_type == "organization":
        # Mirrors the person branch; C3 will unify both behind an applier registry.
        name = str(prop.payload_json.get("name", "")).strip()
        if not name:
            return None
        applied = session.scalar(select(Organization).where(Organization.name == name))
        if applied is None:
            applied = Organization(name=name)
            session.add(applied)
            session.flush()
        applied.source = f"extraction:{prop.source_tier}"
        applied.confidence = prop.confidence
        applied.last_confirmed_at = datetime.now(UTC)
        if should_write_link(prop):
            existing_link = session.scalar(
                select(NetworkLink).where(
                    NetworkLink.organization_id == applied.id,
                    NetworkLink.source == f"extraction:{prop.source_tier}",
                )
            )
            if existing_link is None:
                session.add(
                    NetworkLink(
                        organization_id=applied.id,
                        role=prop.payload_json.get("role"),
                        frequency=prop.payload_json.get("frequency"),
                        recency=prop.payload_json.get("recency"),
                        dimension_code=link_dimension_code(prop),
                        source=f"extraction:{prop.source_tier}",
                        confidence=prop.confidence,
                    )
                )
    elif prop.entity_type == "activity":
        need_id = prop.payload_json.get("need_id")
        title = str(prop.payload_json.get("title", "")).strip()
        if not need_id or not title:
            return None
        need = session.get(Need, int(need_id))
        if need is None:
            return None
        applied = Activity(
            need_id=need.id,
            title=title,
            cadence=prop.payload_json.get("cadence"),
        )
        applied.source = f"extraction:{prop.source_tier}"
        applied.confidence = prop.confidence
        session.add(applied)
        session.flush()

    if applied is None:
        return None
    prop.status = "accepted"
    prop.decided_at = datetime.now(UTC)
    prop.decided_via = decided_via
    session.commit()
    from pre.verdicts import bump_profile_version

    bump_profile_version(session)
    return applied


def reject(session: Session, proposal_id: int) -> bool:
    prop = session.get(ProposedAssertion, proposal_id)
    if prop is None or prop.status != "pending":
        return False
    prop.status = "rejected"
    prop.decided_at = datetime.now(UTC)
    prop.decided_via = "manual"
    session.commit()
    return True


# Pre-approved low-risk auto-accept class (ticket 12): high-confidence tool proposals
# from live connectors. Everything else waits for a human Verdict.
AUTO_ACCEPT_MIN_CONFIDENCE = 0.85
_AUTO_ACCEPT_ENTITY = "tool"


def run_auto_accept(session: Session) -> int:
    """Accept the pre-approved low-risk class; every decision is audit-trailed."""
    applied_count = 0
    pending = list_pending(session)
    for prop in pending:
        eligible = (
            prop.entity_type == _AUTO_ACCEPT_ENTITY
            and prop.confidence >= AUTO_ACCEPT_MIN_CONFIDENCE
        )
        if eligible and accept(session, prop.id, decided_via="auto-rule") is not None:
            applied_count += 1
    return applied_count


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
