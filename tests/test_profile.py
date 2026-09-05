from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.intake import apply_intake_dict
from pre.models import Activity, Goal, Need, Organization, Person, Task, Tool
from pre.profile import (
    dimension_of,
    get_row,
    is_stale,
    iter_texts,
    label_of,
    staleness_cutoff,
    text_of,
)


def _seed_tree(session: Session) -> dict[str, int]:
    apply_intake_dict(
        session,
        {
            "dimensions": [
                {
                    "code": "business",
                    "goals": [
                        {
                            "title": "Outbound machine",
                            "needs": [
                                {
                                    "title": "Lead flow",
                                    "activities": [
                                        {
                                            "title": "Send sequences",
                                            "cadence": "daily",
                                            "tasks": [
                                                {
                                                    "title": "Pull leads",
                                                    "tools": ["Apollo"],
                                                }
                                            ],
                                        },
                                        {"title": "Loose task"},
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ]
        },
    )
    person = Person(display_name="Maya Chen")
    org = Organization(name="Quinn Labs")
    session.add_all([person, org])
    session.commit()
    goal = session.scalars(select(Goal)).one()
    need = session.scalars(select(Need)).one()
    activities = {
        a.title: a.id for a in session.scalars(select(Activity)).all()
    }
    task = session.scalars(select(Task).where(Task.title == "Pull leads")).one()
    tool = session.scalars(select(Tool)).one()
    return {
        "goal": goal.id,
        "need": need.id,
        "activity": activities["Send sequences"],
        "loose_activity": activities["Loose task"],
        "task": task.id,
        "tool": tool.id,
        "person": person.id,
        "organization": org.id,
    }


def test_dimension_of_walks_every_hierarchy_level(session: Session) -> None:
    ids = _seed_tree(session)

    for entity_type in ("goal", "need", "activity", "task", "tool"):
        assert dimension_of(session, entity_type, ids[entity_type]) == "business"


def test_dimension_of_network_and_unknown_is_none(session: Session) -> None:
    ids = _seed_tree(session)

    assert dimension_of(session, "person", ids["person"]) is None
    assert dimension_of(session, "organization", ids["organization"]) is None
    assert dimension_of(session, "tool", 9999) is None
    assert dimension_of(session, "nope", 1) is None


def test_label_of_titles_names_and_fallback(session: Session) -> None:
    ids = _seed_tree(session)

    assert label_of(session, "goal", ids["goal"]) == "Outbound machine"
    assert label_of(session, "tool", ids["tool"]) == "Apollo"
    # Network entities keep the digest fallback (digest items can point at them).
    assert label_of(session, "person", ids["person"]) == f"person:{ids['person']}"
    assert label_of(session, "tool", 9999) == "tool:9999"


def test_text_of_matches_retrieval_formats(session: Session) -> None:
    ids = _seed_tree(session)

    assert text_of(session, "activity", ids["activity"]) == "Send sequences daily"
    assert text_of(session, "tool", ids["tool"]) == "Apollo"
    # Trailing space without cadence is the legacy format — hashes must not move.
    assert text_of(session, "activity", ids["loose_activity"]) == "Loose task "
    with pytest.raises(ValueError, match="unknown entity type"):
        text_of(session, "nope", 1)


def test_iter_texts_covers_all_types_in_index_order(session: Session) -> None:
    ids = _seed_tree(session)

    texts = iter_texts(session)
    kinds = [t for (t, _i, _tx) in texts]

    assert kinds == [
        "goal", "need", "activity", "activity", "task", "tool", "person", "organization",
    ]
    by_key = {(t, i): tx for (t, i, tx) in texts}
    assert by_key[("tool", ids["tool"])] == "Apollo"
    assert by_key[("person", ids["person"])] == "Maya Chen"


def test_is_stale_edges(session: Session) -> None:
    ids = _seed_tree(session)
    tool = session.get(Tool, ids["tool"])
    assert tool is not None

    assert is_stale(session, "tool", ids["tool"]) is False  # just confirmed

    tool.last_confirmed_at = datetime.now(UTC) - timedelta(days=400)
    session.commit()
    assert is_stale(session, "tool", ids["tool"]) is True

    now = datetime.now(UTC)
    assert is_stale(session, "tool", ids["tool"], now=now) is True
    assert is_stale(session, "tool", 9999) is False


def test_is_stale_never_flags_network(session: Session) -> None:
    """Legacy digest behavior: people/organizations never entered the staleness map."""
    ids = _seed_tree(session)
    person = session.get(Person, ids["person"])
    assert person is not None
    person.last_confirmed_at = datetime.now(UTC) - timedelta(days=400)
    session.commit()

    assert is_stale(session, "person", ids["person"]) is False
    assert is_stale(session, "organization", ids["organization"]) is False


def test_is_stale_respects_env_window(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    ids = _seed_tree(session)
    tool = session.get(Tool, ids["tool"])
    assert tool is not None
    tool.last_confirmed_at = datetime.now(UTC) - timedelta(days=40)
    session.commit()

    assert is_stale(session, "tool", ids["tool"]) is False
    monkeypatch.setenv("PRE_STALENESS_DAYS", "30")
    assert is_stale(session, "tool", ids["tool"]) is True


def test_get_row_unknown_type_is_none(session: Session) -> None:
    assert get_row(session, "nope", 1) is None


def test_staleness_cutoff_default_window() -> None:
    now = datetime(2026, 9, 5, tzinfo=UTC)
    assert staleness_cutoff(now) == now - timedelta(days=90)
