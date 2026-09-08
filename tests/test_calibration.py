from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from pre.calibration import calibrate_from_verdicts, render_calibration
from pre.change_corpus import FirehoseEntry, ingest_entries
from pre.digest import assemble_digest
from pre.intake import apply_intake_dict
from pre.judge import ScriptedJudge
from pre.models import Change, DigestItem, ThresholdCell, Tool
from pre.retrieval import index_all
from pre.scoring import judge_change
from pre.verdicts import record_verdict


def _seed(session: Session) -> tuple[Change, Tool]:
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
                                            "tasks": [{"title": "Open Apollo",
                                                       "tools": ["Apollo"]}],
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
    ingest_entries(
        session,
        [FirehoseEntry(product_name="Apollo", title="Apollo pricing change for teams")],
        "lane",
    )
    index_all(session)
    return session.query(Change).one(), session.query(Tool).one()


def _verdict_cycle(session: Session, verdict: str, count: int) -> None:
    for i in range(count):
        entry = FirehoseEntry(
            product_name="Apollo",
            title=f"Apollo pricing change variant {i} for teams",
            published_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        ingest_entries(session, [entry], f"lane-{i}")
        index_all(session)
        from pre.models import Change as ChangeModel

        change = session.query(ChangeModel).order_by(ChangeModel.id.desc()).first()
        tool = session.query(Tool).one()
        judge_change(
            session,
            change.id,
            ScriptedJudge({("tool", tool.id): (85, f"passes {i}")}),
        )
        items = assemble_digest(session, "daily")
        assert items, f"cycle {i} should produce a passing item"
        item = items[0]
        item.profile_version = 1
        session.commit()
        record_verdict(session, item.id, verdict)


def test_calibration_raises_cell_on_high_dismissal(session: Session) -> None:
    _seed(session)
    _verdict_cycle(session, "dismiss", 4)

    adjustments = calibrate_from_verdicts(session)

    assert len(adjustments) == 1
    adj = adjustments[0]
    assert adj.digest_kind == "daily"
    assert adj.dimension_code == "business"
    assert adj.new_score == adj.old_score + 5
    cell = session.query(ThresholdCell).filter_by(digest_kind="daily",
                                                  dimension_code="business").one()
    assert cell.tuning == "calibrated"


def test_calibration_lowers_cell_on_low_dismissal(session: Session) -> None:
    from pre.digest import ensure_matrix

    ensure_matrix(session)
    set_default = session.query(ThresholdCell).filter_by(
        digest_kind="daily", dimension_code="business"
    ).one()
    set_default.min_score = 70
    session.commit()
    _seed(session)
    _verdict_cycle(session, "act", 4)

    adjustments = calibrate_from_verdicts(session)

    assert len(adjustments) == 1
    assert adjustments[0].new_score == 65


def test_manual_cells_never_move(session: Session) -> None:
    from pre.digest import set_cell

    _seed(session)
    set_cell(session, "daily", "business", 75, tuning="manual")
    _verdict_cycle(session, "dismiss", 5)

    adjustments = calibrate_from_verdicts(session)

    assert adjustments == []
    cell = session.query(ThresholdCell).filter_by(digest_kind="daily",
                                                  dimension_code="business").one()
    assert cell.min_score == 75 and cell.tuning == "manual"


def test_small_samples_do_not_move_cells(session: Session) -> None:
    _seed(session)
    _verdict_cycle(session, "dismiss", 2)

    adjustments = calibrate_from_verdicts(session)

    assert adjustments == []


def test_render_calibration_reports_moves(session: Session) -> None:
    _seed(session)
    _verdict_cycle(session, "dismiss", 4)

    text = render_calibration(session)

    assert "CALIBRATION RUN" in text
    assert "-> 85" in text


def test_digest_items_flag_stale_matched_entities(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRE_STALENESS_DAYS", "30")
    from pre.profile import staleness_cutoff

    change, tool = _seed(session)
    # Force the matched Tool's confirmation into the past:
    tool.last_confirmed_at = staleness_cutoff() - __import__("datetime").timedelta(days=1)
    session.commit()
    judge_change(session, change.id, ScriptedJudge({("tool", tool.id): (90, "relied on")}))

    items = assemble_digest(session, "daily")

    assert items[0].stale is True


def test_fresh_entities_not_flagged_stale(session: Session) -> None:
    change, _tool = _seed(session)
    judge_change(session, change.id, ScriptedJudge({}))

    items = assemble_digest(session, "weekly")

    assert all(item.stale is False for item in items)


def test_render_shows_stale_label(session: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    from pre.digest import render_digest
    from pre.profile import staleness_cutoff

    monkeypatch.setenv("PRE_STALENESS_DAYS", "30")
    change, tool = _seed(session)
    tool.last_confirmed_at = staleness_cutoff() - __import__("datetime").timedelta(days=2)
    session.commit()
    judge_change(session, change.id, ScriptedJudge({("tool", tool.id): (90, "relied on")}))
    assemble_digest(session, "daily")

    text = render_digest(session, "daily")

    assert "[STALE PROFILE]" in text


def test_calibration_converges_over_large_aged_history(session: Session) -> None:
    """Issue 17 proof for (a): a big history incl. year-old rows still trains.

    Twelve real end-to-end dismissals plus     bulk backfill to 300 rows with ages
    spread over 400 days; repeated calibration must converge the cell to its
    ceiling with the history intact (nothing pruned, old rows still count).
    """
    from pre.models import VerdictLog

    _seed(session)
    _verdict_cycle(session, "dismiss", 12)

    now = datetime.now(UTC)
    first_item = session.query(DigestItem).order_by(DigestItem.id).first()
    assert first_item is not None
    bulk = [
        VerdictLog(
            digest_item_id=first_item.id,
            change_id=first_item.change_id,
            digest_kind="daily",
            dimension_code="business",
            verdict="dismiss",
            profile_version=1,
            channel="cli",
            recorded_at=now - timedelta(days=i),
        )
        for i in range(1, 289)
    ]
    session.add_all(bulk)
    session.commit()
    assert session.query(VerdictLog).count() == 300

    for _ in range(3):
        calibrate_from_verdicts(session)

    cell = session.query(ThresholdCell).filter_by(
        digest_kind="daily", dimension_code="business"
    ).one()
    assert cell.min_score == 95  # ceiling reached: 80 -> 85 -> 90 -> 95
    assert cell.tuning == "calibrated"
    assert session.query(VerdictLog).count() == 300  # history intact
