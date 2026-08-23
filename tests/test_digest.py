from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session

from pre.change_corpus import FirehoseEntry, ingest_entries
from pre.digest import (
    DEFAULT_MIN_SCORES,
    DIGEST_LIMITS,
    assemble_digest,
    ensure_matrix,
    render_digest,
    render_matrix,
    set_cell,
)
from pre.intake import apply_intake_dict
from pre.judge import ScriptedJudge
from pre.models import Change, DigestItem, ThresholdCell, Tool
from pre.retrieval import index_all
from pre.scoring import judge_change


def _seed(session: Session, tool_name: str = "Apollo", dimension: str = "business") -> Change:
    apply_intake_dict(
        session,
        {
            "dimensions": [
                {
                    "code": dimension,
                    "goals": [
                        {
                            "title": f"Use {tool_name} heavily",
                            "needs": [
                                {
                                    "title": f"{tool_name} reliability",
                                    "activities": [
                                        {
                                            "title": f"Work in {tool_name} daily",
                                            "tasks": [
                                                {"title": f"Open {tool_name}",
                                                 "tools": [tool_name]}
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
    entries = [
        FirehoseEntry(
            product_name=tool_name,
            title=f"{tool_name} pricing change for teams",
            published_at=datetime(2026, 8, 1, tzinfo=UTC),
        )
    ]
    ingest_entries(session, entries, "lane")
    index_all(session)
    return session.query(Change).one()


# --- matrix ---------------------------------------------------------------------


def test_matrix_initializes_34_cells_at_defaults(session: Session) -> None:
    cells = ensure_matrix(session)

    assert len(cells) == 34
    assert len(session.query(ThresholdCell).all()) == 34
    for (kind, _), cell in cells.items():
        assert cell.min_score == DEFAULT_MIN_SCORES[kind]
        assert cell.tuning == "default"


def test_manual_override_wins_and_persists(session: Session) -> None:
    set_cell(session, "daily", "business", 65, tuning="manual")
    set_cell(session, "weekly", "business", 40, tuning="calibrated")

    cells = ensure_matrix(session)
    assert cells[("daily", "business")].min_score == 65
    assert cells[("daily", "business")].tuning == "manual"
    assert cells[("weekly", "business")].min_score == 40
    assert cells[("daily", "financial")].tuning == "default"  # others untouched


def test_set_cell_rejects_out_of_range(session: Session) -> None:
    with pytest.raises(ValueError, match="0-100"):
        set_cell(session, "daily", "business", 101)


def test_render_matrix_lists_every_dimension(session: Session) -> None:
    text = render_matrix(session)
    assert "physical_health" in text
    assert "autonomy_time" in text


# --- assembly ---------------------------------------------------------------------


def test_assembly_respects_dimension_cells(session: Session) -> None:
    change = _seed(session, dimension="business")
    tool = session.query(Tool).one()

    # 60 passes weekly's exploratory default (50) but not daily's precise default (80).
    judge_change(session, change.id, ScriptedJudge({("tool", tool.id): (60, "moderate")}))
    daily = assemble_digest(session, "daily")
    weekly = assemble_digest(session, "weekly")

    assert daily == []
    assert len(weekly) == 1
    item = weekly[0]
    assert item.score == 60
    assert item.entity_type == "tool"
    assert item.entity_label == "Apollo"
    assert item.dimension_code == "business"
    assert item.reasoning == "moderate"

    # Lowering the business/daily cell lets the same score pass daily:
    set_cell(session, "daily", "business", 55, tuning="manual")
    assert len(assemble_digest(session, "daily")) == 1


def test_below_threshold_items_excluded(session: Session) -> None:
    change = _seed(session)
    tool = session.query(Tool).one()
    judge_change(session, change.id, ScriptedJudge({("tool", tool.id): (10, "noise")}))

    assert assemble_digest(session, "weekly") == []
    assert session.query(DigestItem).count() == 0


def test_daily_caps_at_five_highest_scoring(session: Session) -> None:
    names = ["Aaa", "Bbb", "Ccc", "Ddd", "Eee", "Fff"]
    apply_intake_dict(
        session,
        {
            "dimensions": [
                {
                    "code": "business",
                    "goals": [
                        {
                            "title": "Tool stack",
                            "needs": [
                                {
                                    "title": "Reliable tools",
                                    "activities": [
                                        {
                                            "title": "Daily tool usage",
                                            "tasks": [{"title": "Use tools", "tools": names}],
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
    for name in names:
        ingest_entries(
            session,
            [FirehoseEntry(product_name=name, title=f"{name} security patch released")],
            "lane",
        )
    index_all(session)

    tools = {t.name: t for t in session.query(Tool).all()}
    changes = session.query(Change).all()
    mapping: dict[tuple[str, int], tuple[int, str]] = {}
    for change_row in changes:
        tool_name = next(n for n in names if n.lower() in change_row.title.lower())
        rank = names.index(tool_name)
        mapping[("tool", tools[tool_name].id)] = (95 - rank * 3, f"{tool_name} relevant")
    for change_row in changes:
        judge_change(session, change_row.id, ScriptedJudge(mapping))

    daily = assemble_digest(session, "daily")

    assert len(daily) == DIGEST_LIMITS["daily"] == 5
    assert [item.score for item in daily] == sorted(
        (item.score for item in daily), reverse=True
    )
    assert daily[0].entity_label == "Aaa"  # highest score first


def test_undelivered_digest_replaced_on_reassembly(session: Session) -> None:
    change = _seed(session)
    tool = session.query(Tool).one()
    judge_change(session, change.id, ScriptedJudge({("tool", tool.id): (90, "big deal")}))

    assemble_digest(session, "daily")
    assemble_digest(session, "daily")

    assert session.query(DigestItem).count() == 1


def test_render_digest_shows_entity_dimension_reasoning(session: Session) -> None:
    change = _seed(session)
    tool = session.query(Tool).one()
    judge_change(session, change.id, ScriptedJudge({("tool", tool.id): (88, "you rely on Apollo")}))
    assemble_digest(session, "daily")

    text = render_digest(session, "daily")
    assert "Apollo" in text
    assert "(business)" in text
    assert "you rely on Apollo" in text
