"""Profile writes: appliers turning proposals into Profile rows (issue 19, C3).

Queue owns lifecycle (fetch, status, commit, version bump, auto-accept driver);
this module owns application. Shared link-evidence helpers live here; appliers
land unwired one type per commit (B-D) and the flip follows in commit E.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from pre.models import ProposedAssertion

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


__all__ = ["APPLIERS", "Applier", "apply_proposal", "link_dimension_code", "should_write_link"]
