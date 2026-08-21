"""SQLAlchemy models for the Profile: goal hierarchy + Network cluster.

Every row is an assertion and carries provenance (source, confidence, last_confirmed_at)
per the spec's memory model: provenance + decay + confirmation loop.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class ProvenanceMixin:
    source: Mapped[str] = mapped_column(String(64), default="interview")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    last_confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LifeDimension(Base, ProvenanceMixin):
    __tablename__ = "life_dimensions"
    __table_args__ = (
        UniqueConstraint("code"),
        CheckConstraint(
            "satisfaction_score IS NULL OR satisfaction_score BETWEEN 0 AND 10",
            name="ck_satisfaction_range",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(256), default="")
    satisfaction_score: Mapped[int | None] = mapped_column(Integer, nullable=True)

    goals: Mapped[list[Goal]] = relationship(back_populates="dimension")

    def update_satisfaction(self, score: int) -> None:
        if not 0 <= score <= 10:
            raise ValueError("satisfaction_score must be 0-10")
        self.satisfaction_score = score
        self.last_confirmed_at = utcnow()


class Goal(Base, ProvenanceMixin):
    __tablename__ = "goals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dimension_id: Mapped[int] = mapped_column(ForeignKey("life_dimensions.id"))
    title: Mapped[str] = mapped_column(String(256))
    horizon: Mapped[str | None] = mapped_column(String(16), nullable=True)

    dimension: Mapped[LifeDimension] = relationship(back_populates="goals")
    needs: Mapped[list[Need]] = relationship(back_populates="goal")


class Need(Base, ProvenanceMixin):
    __tablename__ = "needs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    goal_id: Mapped[int] = mapped_column(ForeignKey("goals.id"))
    title: Mapped[str] = mapped_column(String(256))
    horizon: Mapped[str | None] = mapped_column(String(16), nullable=True)
    pain_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    openness_to_change: Mapped[str | None] = mapped_column(String(8), nullable=True)

    goal: Mapped[Goal] = relationship(back_populates="needs")
    activities: Mapped[list[Activity]] = relationship(back_populates="need")

    def set_pain(self, level: int) -> None:
        if not 0 <= level <= 10:
            raise ValueError("pain_level must be 0-10")
        self.pain_level = level


class Activity(Base, ProvenanceMixin):
    __tablename__ = "activities"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    need_id: Mapped[int] = mapped_column(ForeignKey("needs.id"))
    title: Mapped[str] = mapped_column(String(256))
    cadence: Mapped[str | None] = mapped_column(String(16), nullable=True)

    need: Mapped[Need] = relationship(back_populates="activities")
    tasks: Mapped[list[Task]] = relationship(back_populates="activity")


class Task(Base, ProvenanceMixin):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    activity_id: Mapped[int] = mapped_column(ForeignKey("activities.id"))
    title: Mapped[str] = mapped_column(String(256))

    activity: Mapped[Activity] = relationship(back_populates="tasks")
    task_tools: Mapped[list[TaskTool]] = relationship(back_populates="task")


class Tool(Base, ProvenanceMixin):
    __tablename__ = "tools"
    __table_args__ = (UniqueConstraint("name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    task_tools: Mapped[list[TaskTool]] = relationship(back_populates="tool")


class TaskTool(Base):
    """A Tool employed by a Task."""

    __tablename__ = "task_tools"

    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), primary_key=True)
    tool_id: Mapped[int] = mapped_column(ForeignKey("tools.id"), primary_key=True)

    task: Mapped[Task] = relationship(back_populates="task_tools")
    tool: Mapped[Tool] = relationship(back_populates="task_tools")


class Person(Base, ProvenanceMixin):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)


class Organization(Base, ProvenanceMixin):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)


class NetworkLink(Base, ProvenanceMixin):
    """The user's relationship context to a Person or Organization (Network cluster)."""

    __tablename__ = "network_links"
    __table_args__ = (
        CheckConstraint(
            "(person_id IS NOT NULL) <> (organization_id IS NOT NULL)",
            name="ck_exactly_one_target",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    person_id: Mapped[int | None] = mapped_column(ForeignKey("people.id"), nullable=True)
    organization_id: Mapped[int | None] = mapped_column(
        ForeignKey("organizations.id"), nullable=True
    )
    role: Mapped[str | None] = mapped_column(String(64), nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(32), nullable=True)
    recency: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dimension_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)

    person: Mapped[Person | None] = relationship()
    organization: Mapped[Organization | None] = relationship()


def provenance_of(obj: Any) -> dict[str, Any]:
    """Read an assertion's provenance as a plain dict."""
    return {
        "source": obj.source,
        "confidence": obj.confidence,
        "last_confirmed_at": obj.last_confirmed_at,
    }
