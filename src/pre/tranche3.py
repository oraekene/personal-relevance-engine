"""Tranche-3 extraction: device exhaust, health/location, work systems.

Completes the ten source tiers. Canonical shapes (v1 contract):

- device JSON:     [{"app": "Chrome", "domain": "figma.com"?, "minutes": 42}, ...]
- health JSON:     {"apps": [{"name": "Strava", "sessions": 12}], ...}
                   (wearables/health apps; location-timeline apps land here too)
- worksystems JSON:[{"system": "hermes", "runs": 31, "last_run": "iso"}, ...]

All three propose Tools tagged with a Life Dimension hint for the coverage report
(health/location -> physical_health, work systems -> business, device -> none).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from pre.models import SourceSyncState
from pre.queue import Proposal, propose

_DOMAIN = re.compile(r"([a-z0-9-]+)\.(com|io|dev|ai|org|net|co|app)")


def _tool_proposal(
    name: str,
    tier: str,
    source_ref: str,
    evidence: dict[str, Any],
    confidence: float,
    dimension_code: str | None,
) -> Proposal:
    return Proposal(
        entity_type="tool",
        payload_key=f"tool:{name.lower()}",
        payload={"name": name, **evidence},
        source_tier=tier,
        source_ref=source_ref,
        confidence=confidence,
        dimension_code=dimension_code,
    )


def parse_device_history(path: str | Path) -> list[Proposal]:
    """App/domain usage minutes -> Tool proposals (no dimension hint)."""
    entries: list[dict[str, Any]] = json.loads(Path(path).read_text(encoding="utf-8"))
    apps: Counter[str] = Counter()
    domains: Counter[str] = Counter()
    for entry in entries:
        app = str(entry.get("app", "")).strip()
        if app:
            apps[app] += int(entry.get("minutes", 0) or 0)
        domain_match = _DOMAIN.search(str(entry.get("domain", "")).lower())
        if domain_match:
            domains[domain_match.group(1).title()] += int(entry.get("minutes", 0) or 0)

    proposals: list[Proposal] = []
    for name, minutes in sorted(apps.items()):
        if minutes <= 0:
            continue
        proposals.append(
            _tool_proposal(name, "device", str(path), {"minutes": minutes},
                           min(0.85, 0.4 + minutes / 600), None)
        )
    for name, minutes in sorted(domains.items()):
        if minutes <= 0 or any(p.payload["name"].lower() == name.lower() for p in proposals):
            continue
        proposals.append(
            _tool_proposal(name, "device", str(path), {"minutes": domains[name]},
                           min(0.8, 0.35 + minutes / 600), None)
        )
    return proposals


def parse_health_export(path: str | Path) -> list[Proposal]:
    """Health/wearable/location apps -> Tool proposals hinted at physical_health."""
    data: dict[str, Any] = json.loads(Path(path).read_text(encoding="utf-8"))
    apps: list[dict[str, Any]] = data.get("apps", [])
    proposals: list[Proposal] = []
    for app in apps:
        name = str(app.get("name", "")).strip()
        sessions = int(app.get("sessions", 0) or 0)
        if not name:
            continue
        proposals.append(
            _tool_proposal(
                name,
                "health",
                str(path),
                {"sessions": sessions},
                min(0.95, 0.6 + 0.02 * sessions),
                "physical_health",
            )
        )
    return proposals


def parse_work_systems(path: str | Path) -> list[Proposal]:
    """The user's own automation systems -> Tool proposals hinted at business."""
    systems: list[dict[str, Any]] = json.loads(Path(path).read_text(encoding="utf-8"))
    proposals: list[Proposal] = []
    for system in systems:
        name = str(system.get("system", "")).strip()
        runs = int(system.get("runs", 0) or 0)
        if not name:
            continue
        proposals.append(
            _tool_proposal(
                name.title(),
                "work-systems",
                str(path),
                {"runs": runs},
                min(0.95, 0.65 + 0.01 * runs),
                "business",
            )
        )
    return proposals


PARSERS = {
    "device": parse_device_history,
    "health": parse_health_export,
    "work-systems": parse_work_systems,
}


def import_tranche3_file(session: Session, kind: str, path: str | Path) -> dict[str, int]:
    """Full history on first connect, deltas after — same mechanics as tranches 1–2."""
    if kind not in PARSERS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {sorted(PARSERS)}")
    tier = kind if kind != "work-systems" else "work-systems"
    source_ref = str(path)

    state = (
        session.query(SourceSyncState).filter_by(tier=tier, source_ref=source_ref).one_or_none()
    )
    if state is None:
        state = SourceSyncState(tier=tier, source_ref=source_ref)
        session.add(state)
        session.flush()

    proposals = PARSERS[kind](Path(path))
    new_count = 0
    strengthened = 0
    for proposal in proposals:
        confidence_before = proposal.confidence
        row = propose(session, proposal)
        if row.observations == 1 and row.status == "pending":
            new_count += 1
        elif row.confidence > confidence_before or row.observations > 1:
            strengthened += 1

    from pre.models import utcnow

    state.records_seen += len(proposals)
    state.last_sync_at = utcnow()
    session.commit()
    return {"proposals_new": new_count, "strengthened": strengthened}
