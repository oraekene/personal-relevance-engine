from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from pre.change_corpus import FirehoseEntry, ingest_entries
from pre.cost_meter import CallRecord, log_call
from pre.db import init_db, make_engine, make_session_factory
from pre.digest import assemble_digest
from pre.intake import apply_intake_dict
from pre.judge import ScriptedJudge
from pre.models import Change, DigestItem, Tool
from pre.ops import (
    backup_database,
    check_provider,
    get_provider_status,
    mark_backup,
    prune_old_changes,
    record_provider_result,
    render_ops_dashboard,
    restore_database,
    spend_by_month,
)
from pre.retrieval import index_all
from pre.scoring import judge_change
from pre.verdicts import record_verdict


def _log_at(session: Session, day: datetime, cents: float) -> None:
    row = log_call(session, CallRecord("judge", "gpt-4o-mini", 100, 50, cents))
    row.called_at = day
    session.commit()


def test_spend_by_month_groups_per_period(session: Session) -> None:
    _log_at(session, datetime(2026, 7, 5, tzinfo=UTC), 10.0)
    _log_at(session, datetime(2026, 7, 20, tzinfo=UTC), 5.0)
    _log_at(session, datetime(2026, 8, 2, tzinfo=UTC), 7.0)

    assert spend_by_month(session) == {"2026-07": 15.0, "2026-08": 7.0}


def test_ops_dashboard_shows_periods_cap_and_health(session: Session) -> None:
    _log_at(session, datetime(2026, 8, 2, tzinfo=UTC), 7.0)

    text = render_ops_dashboard(session)

    assert "OPS DASHBOARD" in text
    assert "2026-08" in text
    assert "monthly cap" in text.lower() or "cap" in text.lower()
    assert "PROVIDER HEALTH" in text
    assert "RETENTION" in text
    assert "BACKUP" in text


def test_provider_pages_on_third_consecutive_failure(session: Session) -> None:
    assert get_provider_status(session).should_page is False

    record_provider_result(session, ok=False)
    record_provider_result(session, ok=False)
    assert get_provider_status(session).should_page is False

    status = record_provider_result(session, ok=False)
    assert status.consecutive_failures == 3
    assert status.should_page is True

    reset = record_provider_result(session, ok=True)
    assert reset.consecutive_failures == 0
    assert reset.should_page is False


def test_check_provider_records_probe_exceptions_as_failures(session: Session) -> None:
    def boom() -> bool:
        raise RuntimeError("provider down")

    status = check_provider(session, boom)
    assert status.consecutive_failures == 1

    status = check_provider(session, lambda: True)
    assert status.consecutive_failures == 0


def _seed_profile_with_tool(session: Session) -> Tool:
    apply_intake_dict(
        session,
        {
            "dimensions": [
                {
                    "code": "business",
                    "goals": [
                        {
                            "title": "Use Apollo heavily",
                            "needs": [
                                {
                                    "title": "Apollo reliability",
                                    "activities": [
                                        {
                                            "title": "Work in Apollo daily",
                                            "tasks": [
                                                {"title": "Open Apollo",
                                                 "tools": ["Apollo"]}
                                            ],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    return session.query(Tool).one()


def test_backup_restore_roundtrip(tmp_path: Path) -> None:
    db_file = tmp_path / "pre.db"
    url = f"sqlite:///{db_file}"
    engine = make_engine(url)
    init_db(engine)
    session = make_session_factory(engine)()
    _seed_profile_with_tool(session)
    ingest_entries(
        session,
        [FirehoseEntry(product_name="Apollo", title="Apollo pricing change")],
        "lane",
    )
    mark_backup(session)
    session.close()
    engine.dispose()

    backup_file = tmp_path / "nightly" / "pre-backup.db"
    backup_database(url, backup_file)
    assert backup_file.is_file()

    wiped = tmp_path / "restored.db"
    restore_database(f"sqlite:///{wiped}", backup_file)

    engine2 = make_engine(f"sqlite:///{wiped}")
    init_db(engine2)
    session2 = make_session_factory(engine2)()
    try:
        assert session2.query(Tool).count() == 1
        assert session2.query(Change).count() == 1
    finally:
        session2.close()
        engine2.dispose()


def test_backup_refuses_memory_db(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="SQLite and Postgres"):
        backup_database("sqlite://", tmp_path / "out.db")


class _FakeRunner:
    """Offline pg_dump/pg_restore stand-in: records argv, fakes the dump file."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> None:
        self.calls.append(argv)
        if argv[0] == "pg_dump":
            Path(argv[-1]).write_text("fake-dump", encoding="utf-8")


def test_backup_postgres_delegates_to_runner(tmp_path: Path) -> None:
    runner = _FakeRunner()
    url = "postgresql://user@host:5432/pre"
    dest = tmp_path / "nightly" / "pre.dump"

    assert backup_database(url, dest, runner=runner) == dest
    assert dest.is_file()  # fake runner wrote it; parents created beforehand
    assert runner.calls == [["pg_dump", url, "-F", "c", "-f", str(dest)]]


def test_restore_postgres_delegates_to_runner(tmp_path: Path) -> None:
    runner = _FakeRunner()
    url = "postgresql://user@host:5432/pre"
    src = tmp_path / "pre.dump"
    src.write_text("fake-dump", encoding="utf-8")

    assert restore_database(url, src, runner=runner) == src
    assert runner.calls == [["pg_restore", "--clean", "--if-exists", "-d", url, str(src)]]


def test_restore_postgres_missing_dump_never_runs(tmp_path: Path) -> None:
    runner = _FakeRunner()

    with pytest.raises(FileNotFoundError, match="backup file not found"):
        restore_database("postgresql://u@h/db", tmp_path / "absent.dump", runner=runner)
    assert runner.calls == []


def test_retention_prunes_old_but_preserves_verdicts(session: Session) -> None:
    tool = _seed_profile_with_tool(session)
    old = datetime.now(UTC) - timedelta(days=400)
    ingest_entries(
        session,
        [
            FirehoseEntry(product_name="Kept", title="Kept verdict news item"),
            FirehoseEntry(product_name="Gone", title="Gone plain news item"),
        ],
        "lane",
    )
    changes = session.query(Change).order_by(Change.id).all()
    for change in changes:
        change.first_seen_at = old
    session.commit()
    index_all(session)

    # Only the first change earns a verdict, so retention must preserve it.
    judge_change(session, changes[0].id, ScriptedJudge({("tool", tool.id): (90, "hit")}))
    items = assemble_digest(session, "daily")
    assert items
    record_verdict(session, items[0].id, "act")

    assert prune_old_changes(session) == 1
    remaining = {c.product_name for c in session.query(Change).all()}
    assert remaining == {"Kept"}


def test_retention_prunes_unverdicted_old_corpus(session: Session) -> None:
    old = datetime.now(UTC) - timedelta(days=400)
    ingest_entries(
        session, [FirehoseEntry(product_name="Stale", title="Stale news item")], "lane"
    )
    change = session.query(Change).one()
    change.first_seen_at = old
    session.commit()

    assert prune_old_changes(session) == 1
    assert session.query(Change).count() == 0
    assert session.query(DigestItem).count() == 0


def test_retention_respects_env_override(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRE_CORPUS_RETENTION_DAYS", "30")
    recent = datetime.now(UTC) - timedelta(days=40)
    ingest_entries(
        session, [FirehoseEntry(product_name="X", title="X news item")], "lane"
    )
    change = session.query(Change).one()
    change.first_seen_at = recent
    session.commit()

    assert prune_old_changes(session) == 1
