"""Ticket 23 commit 3: guided interview wizard with resume."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from pre.models import Goal, LifeDimension, Need


@pytest.fixture()
def client(session: Session):
    from fastapi.testclient import TestClient

    from pre.web import create_app

    engine = session.get_bind()

    def factory() -> Session:
        return sessionmaker(bind=engine, expire_on_commit=False)()

    return TestClient(create_app(factory))


def _complete_step(client, code: str, satisfaction: str = "7") -> None:
    response = client.post(
        f"/interview/{code}",
        data={"satisfaction": satisfaction, "goal_0": "", "goal_0_needs": ""},
        follow_redirects=False,
    )
    assert response.status_code == 303


def test_index_redirects_to_first_step(client) -> None:
    response = client.get("/interview", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/interview/physical_health"


def test_step_renders_dimension(client) -> None:
    response = client.get("/interview/physical_health")

    assert response.status_code == 200
    assert "Physical Health" in response.text
    assert "fitness" in response.text
    assert "Step 1 of 17" in response.text


def test_unknown_dimension_404(client) -> None:
    assert client.get("/interview/narnia").status_code == 404
    assert client.post("/interview/narnia", data={"satisfaction": "5"}).status_code == 404


def test_submit_step_applies_and_advances(client, session: Session) -> None:
    response = client.post(
        "/interview/physical_health",
        data={"satisfaction": "7", "goal_0": "Run daily", "goal_0_needs": "Lead flow\nCash"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.expire_all()
    dim = session.scalars(select(LifeDimension).where(LifeDimension.code == "physical_health")).one()
    assert dim.satisfaction_score == 7
    goal = session.scalars(select(Goal)).one()
    assert goal.title == "Run daily"
    assert {n.title for n in session.scalars(select(Need)).all()} == {"Lead flow", "Cash"}

    resume = client.get("/interview", follow_redirects=False)
    assert resume.headers["location"] == "/interview/mental_wellbeing"


def test_submit_rejects_bad_satisfaction(client, session: Session) -> None:
    assert client.post("/interview/physical_health", data={"satisfaction": "11"}).status_code == 400
    assert client.post("/interview/physical_health", data={}).status_code == 400
    assert session.query(Goal).count() == 0  # failed submit rolls back


def test_double_submit_adds_nothing(client, session: Session) -> None:
    payload = {"satisfaction": "7", "goal_0": "Run daily", "goal_0_needs": "Lead flow"}

    assert client.post("/interview/physical_health", data=payload, follow_redirects=False).status_code == 303
    assert client.post("/interview/physical_health", data=payload, follow_redirects=False).status_code == 303

    assert session.query(Goal).count() == 1
    assert session.query(Need).count() == 1


def test_done_page_shows_gate(client, session: Session) -> None:
    _complete_step(client, "physical_health")

    response = client.get("/interview/done")

    assert response.status_code == 200
    assert "1/17" in response.text
    assert "not passed" in response.text


def test_go_live_blocked_returns_409(client, session: Session) -> None:
    _complete_step(client, "physical_health")

    response = client.post("/interview/go-live", follow_redirects=False)

    assert response.status_code == 409
    assert "Coverage gate has not passed yet." in response.text


def test_api_interview_state(client, session: Session) -> None:
    _complete_step(client, "physical_health")

    response = client.get("/api/interview")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 17
    assert body["done"] == 1
    assert body["gate_passed"] is False
    first = body["steps"][0]
    assert (first["code"], first["done"], first["satisfaction"]) == ("physical_health", True, 7)


def test_api_interview_step_json(client, session: Session) -> None:
    response = client.post(
        "/api/interview/career",
        json={"satisfaction": 8, "goals": [{"title": "Ship it", "needs": ["Focus"]}]},
    )

    assert response.status_code == 200
    assert response.json()["next"] == "physical_health"  # first undone in taxonomy order
    session.expire_all()
    dim = session.scalars(select(LifeDimension).where(LifeDimension.code == "career")).one()
    assert dim.satisfaction_score == 8
    assert session.scalars(select(Goal)).one().title == "Ship it"


def test_api_interview_rejects_bad_input(client) -> None:
    assert client.post("/api/interview/career", json={"satisfaction": 11, "goals": []}).status_code == 400
    assert client.post("/api/interview/narnia", json={"satisfaction": 5, "goals": []}).status_code == 404


def test_api_token_enforced_on_interview(client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRE_API_TOKEN", "secret")

    assert client.get("/api/interview").status_code == 401
    assert client.post("/api/go-live").status_code == 401


def test_api_go_live_blocked_409(client) -> None:
    response = client.post("/api/go-live")

    assert response.status_code == 409
    assert response.json()["detail"]["failures"]
