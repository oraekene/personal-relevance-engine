"""Threshold presets: human-sized controls over the 34-cell matrix (issue 23).

Three positions per Life Dimension map to (daily, weekly) cell pairs and are
recorded as manual tuning, so calibration never overrides a human preset.
Starting parameters like every constant in this system. The raw matrix stays
available for experts (CLI, secondary grid).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from pre.digest import set_cell
from pre.taxonomy import DIMENSIONS_BY_CODE

PRESETS: dict[str, tuple[int, int]] = {
    "quiet": (90, 70),
    "balanced": (80, 50),  # the digest defaults
    "exploratory": (65, 30),
}
PRESET_ORDER = ("quiet", "balanced", "exploratory")


def preset_of(daily: int, weekly: int) -> str | None:
    """Name the preset matching a cell pair, or None for custom tuning."""
    for name, (d, w) in PRESETS.items():
        if (daily, weekly) == (d, w):
            return name
    return None


def apply_preset(session: Session, dimension_code: str, preset: str) -> tuple[int, int]:
    """Set both cells of a dimension from a preset, marked manual. Returns the pair."""
    if preset not in PRESETS:
        raise ValueError(f"preset must be one of {sorted(PRESETS)}, got {preset!r}")
    if dimension_code not in DIMENSIONS_BY_CODE:
        raise ValueError(f"unknown dimension code {dimension_code!r}")
    daily, weekly = PRESETS[preset]
    set_cell(session, "daily", dimension_code, daily, tuning="manual")
    set_cell(session, "weekly", dimension_code, weekly, tuning="manual")
    return daily, weekly


__all__ = ["PRESETS", "PRESET_ORDER", "apply_preset", "preset_of"]
