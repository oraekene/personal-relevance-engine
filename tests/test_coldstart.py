from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from pre.change_corpus import FirehoseEntry, ingest_entries
from pre.coldstart import (
    SHADOW,
    coverage_gate,
    get_mode,
    go_live,
    render_spot_check,
    run_cold_start_cycle,
    set_mode,
)
from pre.digest import assemble_digest
from pre.intake import apply_intake_dict
from pre.judge import ScriptedJudge
from pre.models import Change, DigestItem, Tool
from pre.retrieval import index_all
from pre.scoring import judge_change


def _seed_minimal(session: Session) -> None:
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
                },
                {
                    "code": "financial",
                    "goals": [{"title": "Runway", "needs": [{"title": "Spending visibility"}]}],
                },
            ]
        },
    )


def _seed_corpus(session: Session, n: int) -> list[Change]:
    changes: list[Change] = []
    for i in range(n):
        entry = FirehoseEntry(
            product_name="Apollo",
            title=f"Apollo change number {i} for teams",
            published_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
        ingest_entries(session, [entry], "lane")
        changes.append(session.query(Change).order_by(Change.id.desc()).first())
    index_all(session)
    return changes


# --- shadow mode -----------------------------------------------------------------


def test_default_mode_is_shadow(session: Session) -> None:
    assert get_mode(session) == SHADOW


def test_set_mode_validates(session: Session) -> None:
    with pytest.raises(ValueError, match="mode"):
        set_mode(session, "yolo")


# --- UNSCORED urgent notices --------------------------------------------------------


def test_urgent_unscored_notices_surface_in_shadow(session: Session) -> None:
    _seed_minimal(session)
    ingest_entries(
        session,
        [
            FirehoseEntry(product_name="Apollo", title="Security patch for Apollo dashboard"),
            FirehoseEntry(product_name="Apollo", title="Apollo newsletter August edition"),
        ],
        "lane",
    )
    index_all(session)

    result = run_cold_start_cycle(session)

    assert result["unscored_surfaced"] == 1  # only the security change is urgent
    unscored = (
        session.query(DigestItem).filter(DigestItem.unscored.is_(True)).all()
    )
    assert len(unscored) == 1
    assert "UNSCORED" in unscored[0].reasoning


def test_unscored_notice_not_duplicated_across_cycles(session: Session) -> None:
    _seed_minimal(session)
    ingest_entries(
        session,
        [FirehoseEntry(product_name="Apollo", title="Apollo deprecation of legacy API")],
        "lane",
    )

    first = run_cold_start_cycle(session)
    second = run_cold_start_cycle(session)

    assert first["unscored_surfaced"] == 1
    assert second["unscored_surfaced"] == 0


# --- coverage gate + go-live ----------------------------------------------------------


def test_gate_blocks_a_half_built_profile(session: Session) -> None:
    _seed_minimal(session)
    _seed_corpus(session, 3)  # too few changes; only 2 dimensions; nothing judged

    gate = coverage_gate(session)
    assert gate.passed is False
    assert any("Dimensions touched" in f for f in gate.failures)
    assert any("corpus" in f for f in gate.failures)
    assert any("judged" in f for f in gate.failures)


def test_go_live_refused_until_gate_passes(session: Session) -> None:
    _seed_minimal(session)
    _seed_corpus(session, 3)

    with pytest.raises(PermissionError, match="go-live blocked"):
        go_live(session)
    assert get_mode(session) == SHADOW


def test_go_live_flips_after_gate_passes(session: Session) -> None:
    _seed_minimal(session)
    # Touch more dimensions via interview skeleton:
    apply_intake_dict(
        session,
        {
            "dimensions": [
                {"code": code, "goals": [{"title": f"{code} goal",
                                          "needs": [{"title": f"{code} need"}]}]}
                for code in ("career", "social", "relationship", "family", "housing",
                             "community_civic", "education", "leisure")
            ]
        },
    )
    changes = _seed_corpus(session, 10)
    tool = session.query(Tool).one()
    for change_row in changes[:6]:
        judge_change(session, change_row.id,
                     ScriptedJudge({("tool", tool.id): (85, "watchlist hit")}))

    result = go_live(session)

    assert result.passed is True
    assert get_mode(session) == "live"


def test_shadow_digest_assembles_but_never_delivers(session: Session) -> None:
    _seed_minimal(session)
    _seed_corpus(session, 12)
    tool = session.query(Tool).one()
    changes = session.query(Change).all()
    for change_row in changes:
        judge_change(session, change_row.id,
                     ScriptedJudge({("tool", tool.id): (92, "watchlist hit")}))

    items = assemble_digest(session, "daily")

    assert items, "digest assembles in shadow mode"
    assert all(item.delivered_at is None for item in items)


def test_spot_check_renders_score_bands(session: Session) -> None:
    _seed_minimal(session)
    changes = _seed_corpus(session, 6)
    tool = session.query(Tool).one()
    scores = [90, 85, 60, 55, 20, 5]
    for change_row, value in zip(changes, scores, strict=True):
        judge_change(session, change_row.id,
                     ScriptedJudge({("tool", tool.id): (value, f"gut {value}")}))

    text = render_spot_check(session)

    assert "SPOT CHECK" in text
    assert "[75-99]" in text
    assert "[50-74]" in text
    assert "[0-24]" in text
