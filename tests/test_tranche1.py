from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from pre.models import ProposedAssertion, SourceSyncState, Tool
from pre.parsers import parse_commerce_csv, parse_financial_csv, parse_takeout_activity
from pre.queue import accept, list_pending, propose, reject
from pre.tranche1 import import_source_file
from pre.watchlist import active_watchlist_product_names

FIXTURES = Path(__file__).parent / "fixtures"


# --- parsers (source seam: fixture exports) -----------------------------------


def test_financial_parser_scales_confidence_with_recurrence() -> None:
    proposals = {p.payload["name"]: p for p in parse_financial_csv(FIXTURES / "transactions.csv")}

    assert set(proposals) == {"Netflix", "Github", "Spotify", "Joe Coffee"}
    assert proposals["Netflix"].confidence > proposals["Spotify"].confidence
    assert proposals["Netflix"].payload["observations"] == 3
    assert all(p.source_tier == "financial" for p in proposals.values())


def test_commerce_parser_ignores_physical_goods() -> None:
    proposals = parse_commerce_csv(FIXTURES / "orders.csv")

    names = {p.payload["name"] for p in proposals}
    assert "Figma Inc" in names and "Notion Labs" in names
    assert not any("Keychron" in n for n in names)
    assert all(p.source_tier == "commerce" for p in proposals)


def test_takeout_parser_counts_services() -> None:
    proposals = parse_takeout_activity(FIXTURES / "myactivity.json")

    by_name = {p.payload["name"]: p for p in proposals}
    assert by_name["YouTube"].payload["activity_events"] == 3
    assert by_name["YouTube"].confidence >= by_name["Google Search"].confidence


def test_takeout_parser_rejects_non_list_json(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text('{"oops": true}', encoding="utf-8")
    with pytest.raises(TypeError, match="list"):
        parse_takeout_activity(bad)


# --- queue semantics -----------------------------------------------------------


def test_propose_is_idempotent_and_strengthens(session: Session) -> None:
    from pre.queue import Proposal

    base = Proposal(
        entity_type="tool",
        payload_key="tool:netflix",
        payload={"name": "Netflix"},
        source_tier="financial",
        source_ref="tx.csv",
    )
    first = propose(session, base)
    confidence_before = first.confidence
    observations_before = first.observations
    second = propose(session, base)

    assert first.id == second.id
    assert second.observations == observations_before + 1
    assert second.confidence > confidence_before
    assert session.query(ProposedAssertion).count() == 1


def test_accept_writes_profile_with_provenance(session: Session) -> None:
    from pre.queue import Proposal

    prop = propose(
        session,
        Proposal(
            entity_type="tool",
            payload_key="tool:github",
            payload={"name": "GitHub"},
            source_tier="financial",
            source_ref="tx.csv",
            confidence=0.8,
        ),
    )
    tool = accept(session, prop.id)

    assert tool is not None and tool.name == "GitHub"
    assert tool.source == "extraction:financial"
    assert tool.confidence == pytest.approx(0.8)
    assert prop.status == "accepted"
    assert prop.decided_at is not None


def test_accept_twice_returns_none(session: Session) -> None:
    from pre.queue import Proposal

    prop = propose(
        session,
        Proposal(
            entity_type="tool",
            payload_key="tool:figma",
            payload={"name": "Figma"},
            source_tier="commerce",
            source_ref="orders.csv",
        ),
    )
    assert accept(session, prop.id) is not None
    assert accept(session, prop.id) is None


def test_reject_marks_decision(session: Session) -> None:
    from pre.queue import Proposal

    prop = propose(
        session,
        Proposal(
            entity_type="tool",
            payload_key="tool:nope",
            payload={"name": "Nope"},
            source_tier="takeout",
            source_ref="activity.json",
        ),
    )
    assert reject(session, prop.id) is True
    assert reject(session, prop.id) is False
    assert list_pending(session) == []


# --- tranche-1 orchestration: full history then delta --------------------------


def test_import_full_history_then_delta_is_idempotent(session: Session) -> None:
    first = import_source_file(session, "financial", FIXTURES / "transactions.csv")
    assert first.first_connect is True
    assert first.proposals_new == 4

    second = import_source_file(session, "financial", FIXTURES / "transactions.csv")
    assert second.first_connect is False
    assert second.proposals_new == 0
    assert session.query(ProposedAssertion).count() == 4

    state = session.scalars(select(SourceSyncState)).one()
    assert state.records_seen == 8  # 4 proposals x 2 runs


def test_queue_never_writes_profile_directly(session: Session) -> None:
    import_source_file(session, "financial", FIXTURES / "transactions.csv")
    import_source_file(session, "commerce", FIXTURES / "orders.csv")
    import_source_file(session, "takeout", FIXTURES / "myactivity.json")

    # Nothing accepted yet: the Profile's Tools table must still be empty.
    assert session.query(Tool).count() == 0
    assert len(list_pending(session)) > 0


def test_accepted_extraction_tool_joins_watchlist(session: Session) -> None:
    from pre.watchlist import sync_watchlist

    import_source_file(session, "financial", FIXTURES / "transactions.csv")
    pending = list_pending(session)
    netflix = next(p for p in pending if p.payload_json.get("name") == "Netflix")

    accepted = accept(session, netflix.id)
    assert accepted is not None
    sync_watchlist(session)

    assert active_watchlist_product_names(session) == ["Netflix"]
