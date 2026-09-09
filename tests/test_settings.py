"""Ticket 23 commit 5: threshold presets plus expert grid."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from pre.digest import ensure_matrix
from pre.settings import PRESETS, apply_preset, preset_of


@pytest.fixture()
def client(session: Session):
    from fastapi.testclient import TestClient

    from pre.web import create_app

    engine = session.get_bind()

    def factory() -> Session:
        return sessionmaker(bind=engine, expire_on_commit=False)()

    return TestClient(create_app(factory))


def test_preset_of_roundtrips_and_detects_custom() -> None:
    assert preset_of(80, 50) == "balanced"
    assert preset_of(*PRESETS["quiet"]) == "quiet"
    assert preset_of(77, 50) is None


def test_apply_preset_sets_cells_manual(session: Session) -> None:
    daily, weekly = apply_preset(session, "business", "quiet")

    assert (daily, weekly) == (90, 70)
    cells = ensure_matrix(session)
    assert cells[("daily", "business")].min_score == 90
    assert cells[("daily", "business")].tuning == "manual"

    with pytest.raises(ValueError, match="preset"):
        apply_preset(session, "business", "loud")
    with pytest.raises(ValueError, match="unknown dimension"):
        apply_preset(session, "narnia", "quiet")


def test_settings_page_lists_dimensions(client) -> None:
    response = client.get("/settings")

    assert response.status_code == 200
    assert "Business" in response.text
    assert "Quiet" in response.text
    assert response.text.count("preset_") >= 17


def test_post_preset_applies_and_shows_selected(client, session: Session) -> None:
    response = client.post("/settings", data={"preset_business": "quiet"}, follow_redirects=False)

    assert response.status_code == 303
    session.expire_all()
    cells = ensure_matrix(session)
    assert cells[("daily", "business")].min_score == 90
    assert cells[("weekly", "business")].min_score == 70

    page = client.get("/settings")
    assert "value='quiet' selected" in page.text


def test_post_grid_values_apply_real(client, session: Session) -> None:
    response = client.post(
        "/settings",
        data={"preset_business": "", "cell_daily_business": "77", "cell_weekly_business": "55"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.expire_all()
    cells = ensure_matrix(session)
    assert cells[("daily", "business")].min_score == 77
    assert cells[("weekly", "business")].min_score == 55
    assert cells[("daily", "business")].tuning == "manual"


def test_post_rejects_bad_values(client) -> None:
    assert client.post("/settings", data={"preset_business": "loud"}).status_code == 400
    assert client.post("/settings", data={"cell_daily_business": "999"}).status_code == 400
    assert client.post("/settings", data={"cell_daily_business": "abc"}).status_code == 400


def test_api_settings_roundtrip(client, session: Session) -> None:
    rows = client.get("/api/settings").json()["rows"]

    assert len(rows) == 17
    assert rows[0]["preset"] == "balanced"  # digest defaults

    response = client.post(
        "/api/settings", json={"dimension_code": "business", "preset": "exploratory"}
    )

    assert response.status_code == 200
    assert response.json()["daily"] == 65
    session.expire_all()
    assert client.get("/api/settings").json()["rows"][3]["preset"] == "exploratory"


def test_api_settings_rejects(client, monkeypatch: pytest.MonkeyPatch) -> None:
    assert client.post(
        "/api/settings", json={"dimension_code": "business", "preset": "loud"}
    ).status_code == 400
    assert client.post(
        "/api/settings", json={"dimension_code": "narnia", "preset": "quiet"}
    ).status_code == 404

    monkeypatch.setenv("PRE_API_TOKEN", "secret")
    assert client.get("/api/settings").status_code == 401
