"""Ops baseline (ticket 14): cost dashboard, provider health, backups, retention.

Proportionality (spec): one Postgres, Python workers, cron/APScheduler. No extra
infra. Cron owns scheduling; `pre backup`, `pre prune`, and `pre ops` are the cron
entry points (same pattern as `pre ingest-firehose` in ticket 02).

Backup scope: `pre backup` copies file SQLite DBs (local dev / single-file
deployments) and pg_dumps Postgres URLs (the production target per the spec's
proportionality doctrine) from the same cron slot. The subprocess runner is
injectable, so tests never shell out (same idea as the judge seam).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from pre.cost_meter import monthly_cap_cents, render_costs
from pre.models import Change, ChangeScore, DigestItem, LLMCallLog, SystemFlag, VerdictLog

PROVIDER_FAILURES_KEY = "provider_consec_failures"
BACKUP_KEY = "backup_last_at"
FAILURE_THRESHOLD = 3
DEFAULT_RETENTION_DAYS = 180


def retention_days() -> int:
    """Corpus retention window in days; override with PRE_CORPUS_RETENTION_DAYS."""
    raw = os.environ.get("PRE_CORPUS_RETENTION_DAYS", "")
    try:
        parsed = int(raw) if raw else DEFAULT_RETENTION_DAYS
    except ValueError:
        return DEFAULT_RETENTION_DAYS
    return parsed if parsed > 0 else DEFAULT_RETENTION_DAYS


def spend_by_month(session: Session) -> dict[str, float]:
    """MTD-cost history grouped by YYYY-MM (UTC) for the per-period dashboard."""
    totals: dict[str, float] = {}
    for row in session.scalars(select(LLMCallLog)).all():
        key = f"{row.called_at.year:04d}-{row.called_at.month:02d}"
        totals[key] = totals.get(key, 0.0) + row.cost_usd_cents
    return totals


@dataclass(frozen=True)
class ProviderStatus:
    consecutive_failures: int
    should_page: bool


def get_provider_status(session: Session) -> ProviderStatus:
    flag = session.scalar(
        select(SystemFlag).where(SystemFlag.key == PROVIDER_FAILURES_KEY)
    )
    try:
        count = int(flag.value) if flag is not None else 0
    except ValueError:
        count = 0
    count = max(0, count)
    return ProviderStatus(
        consecutive_failures=count, should_page=count >= FAILURE_THRESHOLD
    )


def record_provider_result(session: Session, ok: bool) -> ProviderStatus:
    """Record one provider probe; success resets the streak, failure extends it."""
    count = 0 if ok else get_provider_status(session).consecutive_failures + 1
    flag = session.scalar(
        select(SystemFlag).where(SystemFlag.key == PROVIDER_FAILURES_KEY)
    )
    if flag is None:
        session.add(SystemFlag(key=PROVIDER_FAILURES_KEY, value=str(count)))
    else:
        flag.value = str(count)
    session.commit()
    return ProviderStatus(
        consecutive_failures=count, should_page=count >= FAILURE_THRESHOLD
    )


def check_provider(session: Session, probe: Callable[[], bool]) -> ProviderStatus:
    """Run an injectable probe (no network in tests) and record the outcome."""
    try:
        ok = bool(probe())
    except Exception:  # noqa: BLE001 -- any probe failure IS a provider failure
        ok = False
    return record_provider_result(session, ok)


def _sqlite_file_for_url(db_url: str) -> Path | None:
    """Resolve a file SQLite URL to its path; None for :memory: or non-SQLite."""
    if not db_url.startswith("sqlite:"):
        return None
    if db_url == "sqlite://" or db_url.rstrip("/").endswith(":memory:"):
        return None
    if db_url.startswith("sqlite:///"):
        path_part = db_url[len("sqlite:///") :]
    elif db_url.startswith("sqlite://"):
        path_part = db_url[len("sqlite://") :]
    else:
        return None
    path_part = path_part.split("?", 1)[0]
    if not path_part or path_part == ":memory:":
        return None
    return Path(path_part)


class CommandRunner(Protocol):
    """Runs one backup command. Tests substitute a fake that records argv."""

    def __call__(self, argv: list[str]) -> None: ...


def _run_command(argv: list[str]) -> None:
    subprocess.run(argv, check=True)


def _is_postgres_url(db_url: str) -> bool:
    return db_url.startswith(("postgresql://", "postgres://"))


def backup_database(
    db_url: str, dest: Path, runner: CommandRunner | None = None
) -> Path:
    """Back up the DB to dest: file copy for SQLite, pg_dump for Postgres."""
    run = runner or _run_command
    src = _sqlite_file_for_url(db_url)
    if src is not None:
        if not src.is_file():
            raise FileNotFoundError(f"database file not found: {src}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return dest
    if _is_postgres_url(db_url):
        dest.parent.mkdir(parents=True, exist_ok=True)
        run(["pg_dump", db_url, "-F", "c", "-f", str(dest)])
        return dest
    raise ValueError(
        f"backup supports file SQLite and Postgres DBs only (got {db_url!r}); "
        "use a file DB, not :memory:"
    )


def restore_database(
    db_url: str, src: Path, runner: CommandRunner | None = None
) -> Path:
    """Restore the DB from a backup: file copy for SQLite, pg_restore for Postgres.

    Returns the restored target file (SQLite) or the applied dump file (Postgres).
    """
    run = runner or _run_command
    if not src.is_file():
        raise FileNotFoundError(f"backup file not found: {src}")
    target = _sqlite_file_for_url(db_url)
    if target is not None:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        return target
    if _is_postgres_url(db_url):
        run(["pg_restore", "--clean", "--if-exists", "-d", db_url, str(src)])
        return src
    raise ValueError(f"restore supports file SQLite and Postgres DBs only (got {db_url!r})")


def mark_backup(session: Session, now: datetime | None = None) -> datetime:
    """Stamp the backup time on the live DB after a successful copy."""
    moment = now or datetime.now(UTC)
    flag = session.scalar(select(SystemFlag).where(SystemFlag.key == BACKUP_KEY))
    if flag is None:
        session.add(SystemFlag(key=BACKUP_KEY, value=moment.isoformat()))
    else:
        flag.value = moment.isoformat()
    session.commit()
    return moment


def last_backup_at(session: Session) -> datetime | None:
    flag = session.scalar(select(SystemFlag).where(SystemFlag.key == BACKUP_KEY))
    if flag is None:
        return None
    try:
        return datetime.fromisoformat(flag.value)
    except ValueError:
        return None


def _as_naive(moment: datetime) -> datetime:
    return moment.replace(tzinfo=None) if moment.tzinfo is not None else moment


def prune_old_changes(session: Session, now: datetime | None = None) -> int:
    """Delete corpus Changes older than the retention window.

    Verdict-carrying Changes are preserved (VerdictLog is the permanent training
    signal for calibration). Dependent scores and ephemeral digest items for pruned
    Changes go with them; LLM cost history is kept as audit.
    """
    moment = now or datetime.now(UTC)
    cutoff = _as_naive(moment) - timedelta(days=retention_days())

    protected: set[int] = set(session.scalars(select(VerdictLog.change_id)).all())
    for item in session.scalars(
        select(DigestItem).where(DigestItem.verdict.is_not(None))
    ).all():
        protected.add(item.change_id)

    victims = [
        change
        for change in session.scalars(select(Change)).all()
        if _as_naive(change.first_seen_at) < cutoff and change.id not in protected
    ]
    if not victims:
        return 0
    victim_ids = {change.id for change in victims}
    session.execute(delete(ChangeScore).where(ChangeScore.change_id.in_(victim_ids)))
    session.execute(delete(DigestItem).where(DigestItem.change_id.in_(victim_ids)))
    for change in victims:
        session.delete(change)
    session.commit()
    return len(victims)


def render_ops_dashboard(session: Session) -> str:
    """One-screen ops floor: spend per period vs cap, provider, retention, backups."""
    lines = ["OPS DASHBOARD", "=" * 60, render_costs(session), ""]
    lines.append("SPEND BY PERIOD (YYYY-MM vs monthly cap):")
    periods = spend_by_month(session)
    if not periods:
        lines.append("  (no calls yet)")
    else:
        cap = monthly_cap_cents()
        for period in sorted(periods):
            lines.append(f"  {period}: {periods[period]:.2f} / {cap} cents")
    lines.append("")
    status = get_provider_status(session)
    lines.append(
        f"PROVIDER HEALTH: {status.consecutive_failures} consecutive "
        f"failures (threshold {FAILURE_THRESHOLD})"
    )
    if status.should_page:
        lines.append("  !! PAGING — provider failed 3+ times in a row")
    else:
        lines.append("  ok")
    lines.append("")
    corpus = session.scalars(select(Change)).all()
    lines.append(
        f"CORPUS RETENTION: keep {retention_days()}d; {len(corpus)} Changes in corpus"
    )
    last = last_backup_at(session)
    if last is None:
        lines.append("LAST BACKUP: (never — run `pre backup --file <dest>` via cron nightly)")
    else:
        lines.append(f"LAST BACKUP: {last.isoformat()}")
    lines.append("NIGHTLY: cron `pre backup --file <dest>` + `pre prune` (retention)")
    return "\n".join(lines)


__all__ = [
    "BACKUP_KEY",
    "DEFAULT_RETENTION_DAYS",
    "FAILURE_THRESHOLD",
    "PROVIDER_FAILURES_KEY",
    "ProviderStatus",
    "backup_database",
    "check_provider",
    "get_provider_status",
    "last_backup_at",
    "mark_backup",
    "prune_old_changes",
    "record_provider_result",
    "render_ops_dashboard",
    "restore_database",
    "retention_days",
    "spend_by_month",
]
