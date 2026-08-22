"""SQLAlchemy models for the Profile: goal hierarchy + Network cluster.

Every row is an assertion and carries provenance (source, confidence, last_confirmed_at)
per the spec's memory model: provenance + decay + confirmation loop.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
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


class WatchlistItem(Base):
    """A product monitored for Changes because it is a Tool in the Profile."""

    __tablename__ = "watchlist"
    __table_args__ = (UniqueConstraint("tool_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tool_id: Mapped[int] = mapped_column(ForeignKey("tools.id"))
    active: Mapped[bool] = mapped_column(default=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    deactivated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    tool: Mapped[Tool] = relationship()


class Change(Base):
    """One deduplicated unit of product news in the corpus.

    Not an assertion about the user, so no ProvenanceMixin: provenance lives in
    `sources` (every firehose/watchlist lane that reported this same Change).
    """

    __tablename__ = "changes"
    __table_args__ = (UniqueConstraint("fingerprint"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_name: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(512))
    url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    change_type: Mapped[str] = mapped_column(String(16))  # feature|improvement|deprecation|pricing|policy|security
    is_pricing: Mapped[bool] = mapped_column(default=False)
    is_deprecation: Mapped[bool] = mapped_column(default=False)
    is_security: Mapped[bool] = mapped_column(default=False)
    fingerprint: Mapped[str] = mapped_column(String(64))
    sources_json: Mapped[list[dict[str, str]]] = mapped_column(
        JSON, default=list
    )  # [{source,url}]
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ProposedAssertion(Base):
    """An extraction candidate awaiting the user's accept/reject decision.

    Never writes the Profile directly (spec: confirmation queue). Uniqueness on
    (payload_key, source_tier) makes re-imports idempotent; repeat observations
    strengthen confidence instead of duplicating.
    """

    __tablename__ = "proposed_assertions"
    __table_args__ = (
        UniqueConstraint("payload_key", "source_tier"),
        CheckConstraint("status IN ('pending','accepted','rejected')", name="ck_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32))  # e.g. 'tool'
    payload_key: Mapped[str] = mapped_column(String(256))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source_tier: Mapped[str] = mapped_column(String(32))  # takeout|financial|commerce|...
    source_ref: Mapped[str] = mapped_column(String(256))
    row_hash: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    observations: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_via: Mapped[str | None] = mapped_column(
        String(16), nullable=True
    )  # 'manual' | 'auto-rule' (audit trail for pre-approved classes)
    dimension_code: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # Life Dimension hint for the coverage report (ticket 11)


class EntityEmbedding(Base):
    """A local embedding vector for one Profile/Network/Change entity.

    Stored as a JSON array so the same code runs on SQLite (dev/tests) and Postgres;
    on Postgres deployments this table migrates to pgvector's `vector` column type for
    index-backed similarity (see README, ticket 03 notes).
    """

    __tablename__ = "entity_embeddings"
    __table_args__ = (
        UniqueConstraint("entity_type", "entity_id"),
        CheckConstraint(
            "entity_type IN ('goal','need','activity','task','tool','person','organization',"
            "'change')",
            name="ck_embedding_entity_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))  # skip re-embedding unchanged text
    dim: Mapped[int] = mapped_column(Integer)
    vector_json: Mapped[list[float]] = mapped_column(JSON)
    embedded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceSyncState(Base):
    """Per-source ingestion state: distinguishes first connect (full history) from deltas."""

    __tablename__ = "source_sync_state"
    __table_args__ = (UniqueConstraint("tier", "source_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    tier: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str] = mapped_column(String(256))
    first_sync_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_sync_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    records_seen: Mapped[int] = mapped_column(Integer, default=0)


class LLMCallLog(Base):
    """Cost meter: one row per LLM API call (ticket 04 doctrine: metered and capped)."""

    __tablename__ = "llm_call_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    purpose: Mapped[str] = mapped_column(String(32))  # 'judge' | ...
    model: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd_cents: Mapped[float] = mapped_column(Float, default=0.0)
    change_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChangeScore(Base):
    """The judge's verdict for one Change × Profile entity pair (stage 3 of the funnel)."""

    __tablename__ = "change_scores"
    __table_args__ = (UniqueConstraint("change_id", "entity_type", "entity_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    change_id: Mapped[int] = mapped_column(ForeignKey("changes.id"))
    entity_type: Mapped[str] = mapped_column(String(16))
    entity_id: Mapped[int] = mapped_column(Integer)
    score: Mapped[int] = mapped_column(Integer)  # 0-100 relevance
    reasoning: Mapped[str] = mapped_column(String(1024), default="")
    judge_name: Mapped[str] = mapped_column(String(64))
    call_id: Mapped[int | None] = mapped_column(ForeignKey("llm_call_log.id"), nullable=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


def provenance_of(obj: Any) -> dict[str, Any]:
    """Read an assertion's provenance as a plain dict."""
    return {
        "source": obj.source,
        "confidence": obj.confidence,
        "last_confirmed_at": obj.last_confirmed_at,
    }
