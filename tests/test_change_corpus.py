from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from pre.change_corpus import classify, fingerprint_for, ingest_entries
from pre.firehose import parse_feed
from pre.models import Change, Tool, WatchlistItem
from pre.watchlist import active_watchlist_product_names, sync_watchlist

FIXTURE_FEED = Path(__file__).parent / "fixtures" / "changelog.xml"


# --- classification heuristics ----------------------------------------------


@pytest.mark.parametrize(
    ("title", "expected_type", "flag"),
    [
        ("Deprecation notice: legacy runners", "deprecation", "is_deprecation"),
        ("Pricing update for Teams plans", "pricing", "is_pricing"),
        ("Security patch for CVE-2026-1234", "security", "is_security"),
        ("Updated privacy policy effective date", "policy", None),
        ("Code search is faster on monorepos", "improvement", None),
        ("New Copilot workspace view", "feature", None),
    ],
)
def test_classify(title: str, expected_type: str, flag: str | None) -> None:
    result = classify(title)
    assert result["change_type"] == expected_type
    if flag:
        assert result[flag] is True


def test_fingerprint_stable_across_formatting() -> None:
    a = fingerprint_for("GitHub", "Pricing  update: Teams!")
    b = fingerprint_for("github", "pricing update teams")
    assert a == b


def test_fingerprint_differs_for_different_changes() -> None:
    assert fingerprint_for("GitHub", "A") != fingerprint_for("GitHub", "B")


# --- feed parsing (source seam: fixture XML) ---------------------------------


def test_parse_rss_fixture() -> None:
    entries = parse_feed(FIXTURE_FEED.read_text(encoding="utf-8"))
    assert len(entries) == 4
    first = entries[0]
    assert first.product_name == "Actions"
    assert first.url is not None and "runner-deprecation" in first.url
    assert first.published_at is not None and first.published_at.year == 2026


# --- ingestion + dedup --------------------------------------------------------


def _ingest_fixture(session: Session) -> None:
    entries = parse_feed(FIXTURE_FEED.read_text(encoding="utf-8"))
    ingest_entries(session, entries, "github-changelog")


def test_ingest_creates_classified_changes(session: Session) -> None:
    _ingest_fixture(session)

    changes = {c.title: c for c in session.query(Change).all()}
    assert len(changes) == 4
    deprecation = next(c for c in changes.values() if c.is_deprecation)
    assert deprecation.change_type == "deprecation"
    pricing = next(c for c in changes.values() if c.is_pricing)
    assert pricing.sources_json[0]["source"] == "github-changelog"


def test_same_change_from_two_lanes_dedupes_with_both_sources(session: Session) -> None:
    from pre.change_corpus import FirehoseEntry

    entry = FirehoseEntry(product_name="Actions", title="Deprecation notice: legacy runners")
    ingest_entries(session, [entry], "lane-a")
    ingest_entries(session, [entry], "lane-b")

    change = session.query(Change).one()
    assert {s["source"] for s in change.sources_json} == {"lane-a", "lane-b"}


def test_reingesting_same_feed_is_idempotent(session: Session) -> None:
    _ingest_fixture(session)
    result = ingest_entries(
        session,
        parse_feed(FIXTURE_FEED.read_text(encoding="utf-8")),
        "github-changelog",
    )
    assert result.created == 0
    assert result.deduped == 4
    assert session.query(Change).count() == 4


# --- watchlist auto-seed -------------------------------------------------------


def test_sync_seeds_every_tool_and_is_idempotent(session: Session) -> None:
    session.add_all([Tool(name="Apollo"), Tool(name="HubSpot")])
    session.commit()

    first = sync_watchlist(session)
    assert first.added == 2
    second = sync_watchlist(session)
    assert second.added == 0
    assert session.query(WatchlistItem).count() == 2
    assert active_watchlist_product_names(session) == ["Apollo", "HubSpot"]


def test_sync_deactivates_removed_tools(session: Session) -> None:
    tool = Tool(name="Apollo")
    session.add(tool)
    session.commit()
    sync_watchlist(session)

    session.delete(tool)
    session.commit()
    result = sync_watchlist(session)

    assert result.deactivated == 1
    assert active_watchlist_product_names(session) == []
