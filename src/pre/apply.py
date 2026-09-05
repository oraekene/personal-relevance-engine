"""Profile writes: appliers turning proposals into Profile rows (issue 19, C3).

Queue owns lifecycle (fetch, status, commit, version bump, auto-accept driver);
this module owns application. Shared link-evidence helpers live here; appliers
land unwired one type per commit (B-D) and the flip follows in commit E.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import (
    Activity,
    Need,
    NetworkLink,
    Organization,
    Person,
    ProposedAssertion,
    Tool,
)

Applier = Callable[[Session, ProposedAssertion], Any | None]

APPLIERS: dict[str, Applier] = {}

_LINK_CONTEXT_KEYS = ("frequency", "recency", "role", "dimension_code")
_LINK_TIERS = ("comms", "social", "contacts", "live-email")


def should_write_link(prop: ProposedAssertion) -> bool:
    """A NetworkLink is evidence: relationship context, a Network tier, or a hint."""
    return (
        any(prop.payload_json.get(key) for key in _LINK_CONTEXT_KEYS)
        or prop.source_tier in _LINK_TIERS
        or prop.dimension_code is not None
    )


def link_dimension_code(prop: ProposedAssertion) -> str | None:
    """One source for link dimensions: the proposal column wins, payload is fallback.

    Extraction hints ride the column, relationship context rides the payload;
    readers (links, coverage) must agree, so the merge lives here.
    """
    if prop.dimension_code is not None:
        return prop.dimension_code
    value = prop.payload_json.get("dimension_code")
    return str(value) if value is not None else None


def apply_proposal(session: Session, prop: ProposedAssertion) -> Any | None:
    """Dispatch one pending proposal to its applier; None when refused or unknown."""
    applier = APPLIERS.get(prop.entity_type)
    if applier is None:
        return None
    return applier(session, prop)


def apply_tool(session: Session, prop: ProposedAssertion) -> Any | None:
    """Get-or-create Tool with extraction provenance; None refuses blank names."""
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
    return applied


APPLIERS["tool"] = apply_tool


def _write_network_link(
    session: Session,
    prop: ProposedAssertion,
    person_id: int | None = None,
    organization_id: int | None = None,
) -> None:
    """Idempotent per (entity, evidence tier) NetworkLink write."""
    if (person_id is None) == (organization_id is None):
        raise ValueError("exactly one of person_id/organization_id is required")
    if not should_write_link(prop):
        return
    source = f"extraction:{prop.source_tier}"
    matches = (
        NetworkLink.person_id == person_id
        if person_id is not None
        else NetworkLink.organization_id == organization_id
    )
    existing = session.scalar(select(NetworkLink).where(matches, NetworkLink.source == source))
    if existing is not None:
        return
    link = NetworkLink(person_id=person_id, organization_id=organization_id)
    link.role = prop.payload_json.get("role")
    link.frequency = prop.payload_json.get("frequency")
    link.recency = prop.payload_json.get("recency")
    link.dimension_code = link_dimension_code(prop)
    link.source = source
    link.confidence = prop.confidence
    session.add(link)


def apply_person(session: Session, prop: ProposedAssertion) -> Any | None:
    """Get-or-create Person plus per-tier NetworkLink; None refuses blank names."""
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
    _write_network_link(session, prop, person_id=applied.id)
    return applied


def apply_organization(session: Session, prop: ProposedAssertion) -> Any | None:
    """Get-or-create Organization plus per-tier NetworkLink; None refuses blank names."""
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
    _write_network_link(session, prop, organization_id=applied.id)
    return applied


APPLIERS["person"] = apply_person
APPLIERS["organization"] = apply_organization


def apply_activity(session: Session, prop: ProposedAssertion) -> Any | None:
    """Create an Activity under its Need; None refuses missing need/title."""
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
    return applied


APPLIERS["activity"] = apply_activity


__all__ = ["APPLIERS", "Applier", "apply_proposal", "link_dimension_code", "should_write_link"]
