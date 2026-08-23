"""Cold-start harness (ticket 13): shadow mode, coverage gate, go-live, spot checks.

Doctrine: cold start before trust. The system runs in shadow mode — judging and digest
assembly work, nothing is delivered — until a Profile coverage check passes.
Urgent Watchlist notices (deprecation/security) surface during cold start labeled
UNSCORED, mirroring the UNTRUSTED-param pattern from the user's radar system.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.coverage import coverage_report
from pre.digest import surface_unscored_urgent
from pre.models import Change, ChangeScore, DigestItem, SystemFlag

MODE_KEY = "mode"
SHADOW = "shadow"
LIVE = "live"

# Go-live gate criteria (deliberately explicit; tune after first real cold start).
MIN_DIMENSIONS_TOUCHED = 8
MIN_CORPUS_CHANGES = 10
MIN_JUDGED_CHANGES = 5


def get_mode(session: Session) -> str:
    flag = session.scalar(select(SystemFlag).where(SystemFlag.key == MODE_KEY))
    return flag.value if flag else SHADOW


def set_mode(session: Session, mode: str) -> None:
    if mode not in (SHADOW, LIVE):
        raise ValueError(f"mode must be '{SHADOW}' or '{LIVE}'")
    flag = session.scalar(select(SystemFlag).where(SystemFlag.key == MODE_KEY))
    if flag is None:
        session.add(SystemFlag(key=MODE_KEY, value=mode))
    else:
        flag.value = mode
    session.commit()


@dataclass(frozen=True)
class GateResult:
    passed: bool
    dimensions_touched: int
    corpus_changes: int
    judged_changes: int
    failures: list[str]


def coverage_gate(session: Session) -> GateResult:
    report = coverage_report(session)
    touched = sum(
        1
        for cov in report
        if cov.goals or cov.needs or cov.activities or cov.tasks or cov.tiers
    )
    corpus = len(session.scalars(select(Change)).all())
    judged = len(
        session.scalars(
            select(ChangeScore.change_id).distinct()
        ).all()
    )

    failures: list[str] = []
    if touched < MIN_DIMENSIONS_TOUCHED:
        failures.append(
            f"only {touched}/{len(report)} Life Dimensions touched "
            f"(need {MIN_DIMENSIONS_TOUCHED})"
        )
    if corpus < MIN_CORPUS_CHANGES:
        failures.append(f"corpus has {corpus} Changes (need {MIN_CORPUS_CHANGES})")
    if judged < MIN_JUDGED_CHANGES:
        failures.append(f"only {judged} Changes judged (need {MIN_JUDGED_CHANGES})")

    return GateResult(
        passed=not failures,
        dimensions_touched=touched,
        corpus_changes=corpus,
        judged_changes=judged,
        failures=failures,
    )


def go_live(session: Session) -> GateResult:
    """Flip shadow -> live, but only when the coverage gate passes."""
    result = coverage_gate(session)
    if not result.passed:
        raise PermissionError("go-live blocked: " + "; ".join(result.failures))
    set_mode(session, LIVE)
    return result


def run_cold_start_cycle(session: Session, kind: str = "daily") -> dict[str, int]:
    """One shadow-mode cycle: surface urgent UNSCORED notices, assemble the shadow digest."""
    surfaced = surface_unscored_urgent(session, kind=kind)
    items = len(
        session.scalars(
            select(DigestItem).where(DigestItem.digest_kind == kind)
        ).all()
    )
    return {"unscored_surfaced": surfaced, "items_assembled": items}


def render_spot_check(session: Session, band_size: int = 25) -> str:
    """Group judged scores into bands so the user can sanity-check calibration."""
    lines = ["SPOT CHECK — do these scores match your gut?", "=" * 60]
    rows = session.scalars(select(ChangeScore).order_by(ChangeScore.score.desc())).all()
    if not rows:
        lines.append("  (nothing judged yet)")
        return "\n".join(lines)

    bands: dict[int, list[ChangeScore]] = {}
    for row in rows:
        bands.setdefault(row.score // band_size * band_size, []).append(row)

    for top in sorted(bands, reverse=True):
        lines.append(f"\n[{top}-{top + band_size - 1}] ({len(bands[top])} items)")
        for row in bands[top][:5]:
            change = session.get(Change, row.change_id)
            title = f"{change.product_name}: {change.title}" if change else "?"
            lines.append(f"  {row.score:>3}/100  {title}")
            lines.append(f"          {row.reasoning}")
    return "\n".join(lines)


__all__ = [
    "LIVE",
    "MODE_KEY",
    "SHADOW",
    "GateResult",
    "coverage_gate",
    "get_mode",
    "go_live",
    "render_spot_check",
    "run_cold_start_cycle",
    "set_mode",
]
