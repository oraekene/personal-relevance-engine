from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from pre.change_corpus import FirehoseEntry, ingest_entries
from pre.digest import assemble_digest
from pre.intake import apply_intake_dict
from pre.judge import ScriptedJudge
from pre.models import DigestItem
from pre.retrieval import index_all
from pre.scoring import judge_change
from pre.verdicts import (
    get_profile_version,
    record_verdict,
    render_verdict_summary,
)


def _seed_and_assemble(session: Session, profile_version: int | None = None) -> DigestItem:
    from pre.models import Change, Tool

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
    change = session.query(Change).one()
    tool = session.query(Tool).one()
    judge_change(session, change.id, ScriptedJudge({("tool", tool.id): (90, "you rely on Apollo")}))
    item = assemble_digest(session, "daily")[0]
    if profile_version is not None:
        item.profile_version = profile_version
        session.commit()
    return item


def test_profile_version_starts_at_zero(session: Session) -> None:
    assert get_profile_version(session) == 0


def test_intake_bumps_profile_version(session: Session) -> None:
    apply_intake_dict(
        session,
        {"dimensions": [{"code": "business", "goals": [{"title": "g"}]}]},
    )
    assert get_profile_version(session) == 1


def test_accept_bumps_profile_version(session: Session) -> None:
    from pre.queue import Proposal, accept, propose

    prop = propose(
        session,
        Proposal(
            entity_type="tool",
            payload_key="tool:zapier",
            payload={"name": "Zapier"},
            source_tier="financial",
            source_ref="x.csv",
        ),
    )
    before = get_profile_version(session)
    accept(session, prop.id)
    assert get_profile_version(session) == before + 1


def test_digest_item_stamps_profile_version_at_assembly(session: Session) -> None:
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
    version_after_intake = get_profile_version(session)
    ingest_entries(
        session,
        [FirehoseEntry(product_name="Apollo", title="Apollo pricing change for teams")],
        "lane",
    )
    index_all(session)
    from pre.models import Change, Tool

    change = session.query(Change).one()
    tool = session.query(Tool).one()
    judge_change(session, change.id, ScriptedJudge({("tool", tool.id): (90, "relied on")}))

    item = assemble_digest(session, "daily")[0]

    assert item.profile_version == version_after_intake


def test_record_verdict_act_and_dismiss(session: Session) -> None:
    item = _seed_and_assemble(session)

    log_row = record_verdict(session, item.id, "act")

    assert log_row.verdict == "act"
    assert log_row.profile_version == (item.profile_version or 0)
    assert log_row.channel == "cli"
    refreshed = session.get(DigestItem, item.id)
    assert refreshed.verdict == "act"
    assert refreshed.verdict_at is not None


def test_dismiss_also_records(session: Session) -> None:
    item = _seed_and_assemble(session)
    record_verdict(session, item.id, "dismiss")
    assert session.get(DigestItem, item.id).verdict == "dismiss"


def test_exactly_one_verdict_per_item(session: Session) -> None:
    item = _seed_and_assemble(session)
    record_verdict(session, item.id, "act")

    with pytest.raises(ValueError, match="already has a verdict"):
        record_verdict(session, item.id, "dismiss")


def test_invalid_choice_rejected(session: Session) -> None:
    item = _seed_and_assemble(session)

    with pytest.raises(ValueError, match="act"):
        record_verdict(session, item.id, "maybe")


def test_unknown_item_rejected(session: Session) -> None:
    with pytest.raises(ValueError, match="not found"):
        record_verdict(session, 9999, "act")


def test_verdict_summary_render(session: Session) -> None:
    item = _seed_and_assemble(session, profile_version=3)
    record_verdict(session, item.id, "dismiss", channel="web")

    text = render_verdict_summary(session)

    assert "1 recorded" in text
    assert "dismiss" in text
    assert "pv3" in text
    assert "web" in text
