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

from pre.queue import Proposal

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
