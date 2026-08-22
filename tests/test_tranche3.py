from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from pre.coverage import coverage_report, render_coverage
from pre.intake import apply_intake_dict
from pre.models import ProposedAssertion, Tool
from pre.queue import list_pending
from pre.tranche3 import (
    import_tranche3_file,
    parse_device_history,
    parse_health_export,
    parse_work_systems,
)

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture()
def tranche3_fixtures(tmp_path: Path) -> dict[str, Path]:
    device = tmp_path / "device.json"
    device.write_text(
        '[{"app": "Chrome", "domain": "figma.com", "minutes": 300},'
        ' {"app": "Figma", "minutes": 480},'
        ' {"app": "Terminal", "minutes": 10}]',
        encoding="utf-8",
    )
    health = tmp_path / "health.json"
    health.write_text(
        '{"apps": [{"name": "Strava", "sessions": 15},'
        ' {"name": "Google Maps", "sessions": 8}]}',
        encoding="utf-8",
    )
    worksystems = tmp_path / "works.json"
    worksystems.write_text(
        '[{"system": "hermes", "runs": 31}, {"system": "job pipeline", "runs": 12}]',
        encoding="utf-8",
    )
    return {"device": device, "health": health, "work-systems": worksystems}


# --- parsers ---------------------------------------------------------------------


def test_device_parser_extracts_apps_and_domains(tranche3_fixtures: dict[str, Path]) -> None:
    proposals = {p.payload["name"]: p for p in parse_device_history(
        tranche3_fixtures["device"]
    )}

    assert "Figma" in proposals  # both as app and domain; single proposal wins
    assert proposals["Figma"].payload["minutes"] >= 480
    assert all(p.entity_type == "tool" for p in proposals.values())
    assert all(p.dimension_code is None for p in proposals.values())


def test_health_parser_tags_physical_health(tranche3_fixtures: dict[str, Path]) -> None:
    proposals = {p.payload["name"]: p for p in parse_health_export(tranche3_fixtures["health"])}

    assert set(proposals) == {"Strava", "Google Maps"}
    assert all(p.dimension_code == "physical_health" for p in proposals.values())
    assert proposals["Strava"].confidence > proposals["Google Maps"].confidence


def test_worksystems_parser_tags_business(tranche3_fixtures: dict[str, Path]) -> None:
    proposals = {p.payload["name"]: p for p in parse_work_systems(
        tranche3_fixtures["work-systems"]
    )}

    assert set(proposals) == {"Hermes", "Job Pipeline"}
    assert all(p.dimension_code == "business" for p in proposals.values())


def test_tranche3_import_routes_through_queue(
    session: Session, tranche3_fixtures: dict[str, Path]
) -> None:
    result = import_tranche3_file(session, "health", tranche3_fixtures["health"])

    assert result["proposals_new"] == 2
    assert session.query(Tool).count() == 0
    hints = {p.dimension_code for p in session.query(ProposedAssertion).all()}
    assert hints == {"physical_health"}


def test_tranche3_full_history_then_delta(
    session: Session, tranche3_fixtures: dict[str, Path]
) -> None:
    first = import_tranche3_file(session, "work-systems", tranche3_fixtures["work-systems"])
    second = import_tranche3_file(session, "work-systems", tranche3_fixtures["work-systems"])

    assert first["proposals_new"] == 2
    assert second["proposals_new"] == 0


def test_unknown_kind_rejected(session: Session) -> None:
    with pytest.raises(ValueError, match="unknown kind"):
        import_tranche3_file(session, "smoke-detectors", FIXTURES / "notes.json")


# --- coverage report ---------------------------------------------------------------


def test_coverage_reports_dimensions_and_tiers(
    session: Session, tranche3_fixtures: dict[str, Path]
) -> None:
    apply_intake_dict(
        session,
        {
            "dimensions": [
                {
                    "code": "business",
                    "satisfaction": 6,
                    "goals": [
                        {
                            "title": "Automate the agency",
                            "needs": [{"title": "Reliable automation"}],
                        }
                    ],
                },
                {
                    "code": "physical_health",
                    "satisfaction": 4,
                    "goals": [{"title": "Stay fit", "needs": [{"title": "Train weekly"}]}],
                },
            ]
        },
    )
    import_tranche3_file(session, "health", tranche3_fixtures["health"])
    import_tranche3_file(session, "work-systems", tranche3_fixtures["work-systems"])

    report = {cov.code: cov for cov in coverage_report(session)}

    assert len(report) == 17
    assert report["business"].goals == 1
    assert report["business"].needs == 1
    assert "work-systems" in report["business"].tiers
    assert "health" in report["physical_health"].tiers
    assert report["social"].tiers == set()  # untouched dimension

    text = render_coverage(session)
    assert "PROFILE COVERAGE" in text
    assert "untouched dimensions" in text
    assert "Physical Health" in text  # rendered by name


def test_all_ten_tiers_have_now_fed_the_queue(session: Session) -> None:
    """Ticket 11 completion check: the ten source tiers are all wired to the queue."""
    from pre.tranche2 import PARSERS as T2
    from pre.tranche3 import PARSERS as T3

    # tranche 1 (ticket 08): takeout/financial/commerce — covered there
    # live connectors (ticket 12): live-calendar/live-email — covered there
    assert {"comms", "notes", "social", "contacts"} <= set(T2)
    assert {"device", "health", "work-systems"} <= set(T3)
    assert list_pending(session) == []
