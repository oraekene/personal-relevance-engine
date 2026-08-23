"""Calibration loop (ticket 07): Verdicts re-fit threshold cells; staleness flags.

Heuristic (starting parameters, like every constant in this system):
- a cell needs at least MIN_SAMPLE verdicts on passed items in its dimension before it
  moves;
- dismissal rate above HIGH_DISMISSAL raises the cell by ADJUST_STEP (noise floor rises);
- dismissal rate below LOW_DISMISSAL lowers it by ADJUST_STEP (let more through);
- manual cells are never touched — hand overrides win, per the spec.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.digest import ensure_matrix
from pre.models import VerdictLog

MIN_SAMPLE = 4
HIGH_DISMISSAL = 0.6
LOW_DISMISSAL = 0.25
ADJUST_STEP = 5
SCORE_FLOOR = 20
SCORE_CEILING = 95


@dataclass(frozen=True)
class CellAdjustment:
    digest_kind: str
    dimension_code: str
    old_score: int
    new_score: int
    reason: str


def _adjust(current: int, dismissal_rate: float) -> tuple[int, str] | None:
    if dismissal_rate > HIGH_DISMISSAL:
        new = min(SCORE_CEILING, current + ADJUST_STEP)
        return (new, f"dismissal rate {dismissal_rate:.0%} too high") if new != current else None
    if dismissal_rate < LOW_DISMISSAL:
        new = max(SCORE_FLOOR, current - ADJUST_STEP)
        return (new, f"dismissal rate {dismissal_rate:.0%} low — widen the net") if (
            new != current
        ) else None
    return None


def calibrate_from_verdicts(session: Session) -> list[CellAdjustment]:
    """Re-fit non-manual cells from the VerdictLog. Returns what moved.

    Samples VerdictLog directly — it snapshots digest kind and dimension per verdict,
    so calibration survives DigestItem cleanup.
    """
    cells = ensure_matrix(session)
    adjustments: list[CellAdjustment] = []

    samples: dict[tuple[str, str], list[VerdictLog]] = defaultdict(list)
    for log_row in session.scalars(select(VerdictLog)).all():
        if not log_row.dimension_code or not log_row.digest_kind:
            continue
        samples[(log_row.digest_kind, log_row.dimension_code)].append(log_row)

    for key, sample_rows in sorted(samples.items()):
        cell = cells.get(key)
        if cell is None or cell.tuning == "manual":
            continue  # overrides win over calibration
        if len(sample_rows) < MIN_SAMPLE:
            continue
        dismissals = sum(1 for r in sample_rows if r.verdict == "dismiss")
        rate = dismissals / len(sample_rows)
        move = _adjust(cell.min_score, rate)
        if move is None:
            continue
        old_score = cell.min_score
        new_score, reason = move
        cell.min_score = new_score
        cell.tuning = "calibrated"
        adjustments.append(
            CellAdjustment(key[0], key[1], old_score, new_score, reason)
        )
    session.commit()
    return adjustments


def render_calibration(session: Session) -> str:
    adjustments = calibrate_from_verdicts(session)
    lines = ["CALIBRATION RUN", "=" * 60]
    if not adjustments:
        lines.append("  (no cells moved — need >= "
                     f"{MIN_SAMPLE} verdicts per dimension, or cells are manual)")
    for adj in adjustments:
        lines.append(
            f"  {adj.digest_kind}/{adj.dimension_code}: {adj.old_score} -> {adj.new_score} "
            f"({adj.reason})"
        )
    return "\n".join(lines)
