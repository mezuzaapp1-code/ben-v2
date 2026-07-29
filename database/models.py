import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA = "ben"


class Base(DeclarativeBase):
    pass


class Thread(Base):
    __tablename__ = "threads"
    __table_args__ = (
        Index("ix_threads_org", "org_id"),
        Index("ix_threads_created", "created_at"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        Index("ix_messages_org", "org_id"),
        Index("ix_messages_thread", "thread_id"),
        Index("ix_messages_created", "created_at"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.threads.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class CognitiveEvent(Base):
    __tablename__ = "cognitive_events"
    __table_args__ = (
        CheckConstraint("type IN ('challenge_raised','contradiction_found','insight_discovered','decision_made','assumption_rejected')"),
        Index("ix_ce_org", "org_id"),
        Index("ix_ce_thread", "thread_id"),
        Index("ix_ce_created", "created_at"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.threads.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class KnowledgeObject(Base):
    __tablename__ = "knowledge_objects"
    __table_args__ = (
        CheckConstraint(
            "type IN ('problem','hypothesis','insight','decision','contradiction','synthesis')"
        ),
        CheckConstraint("status IN ('active','evolving','resolved','rejected','archived')"),
        Index("ix_ko_org", "org_id"),
        Index("ix_ko_created", "created_at"),
        Index("ix_ko_updated", "updated_at"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        CheckConstraint("relation IN ('contradicts','supports','evolved_from','challenges','resolves','depends_on')"),
        Index("ix_rel_org", "org_id"),
        Index("ix_rel_src", "source_object_id"),
        Index("ix_rel_tgt", "target_object_id"),
        Index("ix_rel_created", "created_at"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    source_object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.knowledge_objects.id", ondelete="CASCADE"), nullable=False)
    relation: Mapped[str] = mapped_column(String(64), nullable=False)
    target_object_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey(f"{SCHEMA}.knowledge_objects.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class LedgerDecision(Base):
    __tablename__ = "ledger_decisions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('draft','pending','approved','rejected','superseded')",
            name="ck_ledger_decisions_status",
        ),
        Index("ix_ledger_decisions_org", "org_id"),
        Index("ix_ledger_decisions_org_subject_created", "org_id", "subject", "created_at"),
        Index("ix_ledger_decisions_org_status", "org_id", "status"),
        Index("ix_ledger_decisions_supersedes", "supersedes_decision_id"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    subject: Mapped[str] = mapped_column(String(128), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    policy_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    evidence: Mapped[list[Any] | dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    supersedes_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.ledger_decisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LedgerApproval(Base):
    __tablename__ = "ledger_approvals"
    __table_args__ = (
        CheckConstraint("verdict IN ('approve','reject')", name="ck_ledger_approvals_verdict"),
        Index("ix_ledger_approvals_org", "org_id"),
        Index("ix_ledger_approvals_decision_created", "decision_id", "created_at"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.ledger_decisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    verdict: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class LedgerAction(Base):
    __tablename__ = "ledger_actions"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('done','failed','rolled_back')",
            name="ck_ledger_actions_outcome",
        ),
        Index("ix_ledger_actions_org", "org_id"),
        Index("ix_ledger_actions_decision_created", "decision_id", "created_at"),
        Index("ix_ledger_actions_reverses", "reverses_action_id"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.ledger_decisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    proof_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    reverses_action_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.ledger_actions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


# BEN Log v1 event types (reasoning continuity primitives — not workflow states).
BEN_LOG_EVENT_TYPES = (
    "prompt",
    "response",
    "decision",
    "rejection",
    "unresolved",
    "next_step",
    "context",
    "note",
)
BEN_LOG_SOURCES = ("chat", "council", "human", "system")


PROJECT_STATUSES = ("active", "on_hold", "completed", "archived")
PROJECT_MEMBER_TYPES = ("EMPLOYEE", "VENDOR")
PROJECT_TASK_STATUSES = ("todo", "in_progress", "blocked", "done")
PROJECT_TASK_PRIORITIES = ("low", "medium", "high", "urgent")
FINANCIAL_LEDGER_ENTRY_TYPES = ("INCOME", "EXPENSE")
FINANCIAL_LEDGER_STATUSES = ("pending", "recorded", "paid", "cancelled")


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({','.join(repr(s) for s in PROJECT_STATUSES)})",
            name="ck_projects_status",
        ),
        Index("ix_projects_org", "org_id"),
        Index("ix_projects_org_status", "org_id", "status"),
        Index("ix_projects_created", "created_at"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProjectMember(Base):
    __tablename__ = "project_members"
    __table_args__ = (
        CheckConstraint(
            f"member_type IN ({','.join(repr(t) for t in PROJECT_MEMBER_TYPES)})",
            name="ck_project_members_member_type",
        ),
        Index("ix_project_members_org", "org_id"),
        Index("ix_project_members_project", "project_id"),
        Index("ix_project_members_org_project", "org_id", "project_id"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    member_type: Mapped[str] = mapped_column(String(16), nullable=False)
    role: Mapped[str | None] = mapped_column(String(128), nullable=True)
    hourly_rate: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    contact_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ProjectTask(Base):
    __tablename__ = "project_tasks"
    __table_args__ = (
        CheckConstraint(
            f"status IN ({','.join(repr(s) for s in PROJECT_TASK_STATUSES)})",
            name="ck_project_tasks_status",
        ),
        CheckConstraint(
            f"priority IN ({','.join(repr(p) for p in PROJECT_TASK_PRIORITIES)})",
            name="ck_project_tasks_priority",
        ),
        Index("ix_project_tasks_org", "org_id"),
        Index("ix_project_tasks_project", "project_id"),
        Index("ix_project_tasks_org_project", "org_id", "project_id"),
        Index("ix_project_tasks_assigned_to", "assigned_to"),
        Index("ix_project_tasks_due_date", "due_date"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="todo")
    priority: Mapped[str] = mapped_column(String(16), nullable=False, server_default="medium")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    assigned_to: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.project_members.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FinancialLedger(Base):
    __tablename__ = "financial_ledger"
    __table_args__ = (
        CheckConstraint(
            f"entry_type IN ({','.join(repr(t) for t in FINANCIAL_LEDGER_ENTRY_TYPES)})",
            name="ck_financial_ledger_entry_type",
        ),
        CheckConstraint(
            f"status IN ({','.join(repr(s) for s in FINANCIAL_LEDGER_STATUSES)})",
            name="ck_financial_ledger_status",
        ),
        Index("ix_financial_ledger_org", "org_id"),
        Index("ix_financial_ledger_project", "project_id"),
        Index("ix_financial_ledger_org_project", "org_id", "project_id"),
        Index("ix_financial_ledger_due_date", "due_date"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.projects.id", ondelete="CASCADE"),
        nullable=False,
    )
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    entry_type: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[float] = mapped_column(Numeric(14, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default="USD")
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class BenLogEvent(Base):
    __tablename__ = "ben_log_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('prompt','response','decision','rejection','unresolved','next_step','context','note')",
            name="ck_ben_log_events_event_type",
        ),
        CheckConstraint(
            "source IN ('chat','council','human','system')",
            name="ck_ben_log_events_source",
        ),
        Index("ix_ben_log_events_org", "org_id"),
        Index("ix_ben_log_events_org_thread_created", "org_id", "thread_id", "created_at"),
        Index("ix_ben_log_events_org_thread_type", "org_id", "thread_id", "event_type"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    thread_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.threads.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class NewsSource(Base):
    """System-managed RSS/Atom feed registry row (metadata only; no collector logic)."""

    __tablename__ = "news_sources"
    __table_args__ = (
        UniqueConstraint("feed_url", name="uq_news_sources_feed_url"),
        Index("ix_news_sources_enabled", "enabled"),
        Index("ix_news_sources_category", "category"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    feed_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    language: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'en'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class NewsArticle(Base):
    """Ingested feed item metadata only — no full article body storage."""

    __tablename__ = "news_articles"
    __table_args__ = (
        UniqueConstraint("source_id", "guid", name="uq_news_articles_source_guid"),
        Index("ix_news_articles_published_id", "published_at", "id"),
        Index("ix_news_articles_category_published_id", "category", "published_at", "id"),
        Index("ix_news_articles_source_published_id", "source_id", "published_at", "id"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.news_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    guid: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str] = mapped_column(String(1024), nullable=False)
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class NewsEvent(Base):
    """Canonical News event (clustering unit). Consumers read EventPackages, not this row alone."""

    __tablename__ = "news_events"
    __table_args__ = (
        Index("ix_news_events_lifecycle_updated", "lifecycle", "material_updated_at", "id"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    lifecycle: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'open'"))
    headline: Mapped[str] = mapped_column(String(1024), nullable=False)
    happened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    material_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    current_package_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class NewsEventPackage(Base):
    """Versioned EventPackage v1 payload (JSONB). Product consumers read this contract only."""

    __tablename__ = "news_event_packages"
    __table_args__ = (
        UniqueConstraint("event_id", "package_version", name="uq_news_event_packages_event_version"),
        Index("ix_news_event_packages_event_version", "event_id", "package_version"),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.news_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    package_version: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class NewsClaimExtraction(Base):
    """Per-article claim extraction run status (E1). Operator inspection only."""

    __tablename__ = "news_claim_extractions"
    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "extractor_version",
            name="uq_news_claim_extractions_article_version",
        ),
        Index("ix_news_claim_extractions_article_status", "article_id", "status"),
        CheckConstraint(
            "status IN ('pending','succeeded','failed','skipped')",
            name="ck_news_claim_extractions_status",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.news_articles.id", ondelete="CASCADE"),
        nullable=False,
    )
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    content_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'pending'"))
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    claim_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    error_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NewsClaim(Base):
    """Atomic article-scoped claim (E1). Not an Event; not a product feed source.

    SoR classification: epistemic_type + semantic_domains + source_strength.
    claim_type / stored role intentionally absent.
    """

    __tablename__ = "news_claims"
    __table_args__ = (
        UniqueConstraint(
            "article_id",
            "extractor_version",
            "claim_fingerprint",
            name="uq_news_claims_article_version_fingerprint",
        ),
        Index("ix_news_claims_article_version", "article_id", "extractor_version"),
        Index("ix_news_claims_article_epistemic", "article_id", "epistemic_type"),
        Index("ix_news_claims_source_strength", "source_strength"),
        CheckConstraint(
            "epistemic_type IN ("
            "'fact','attributed_statement','allegation','prediction','opinion','correction')",
            name="ck_news_claims_epistemic_type",
        ),
        CheckConstraint(
            "source_strength IN ("
            "'official','wire','major_media','industry_media','blog','social','unknown')",
            name="ck_news_claims_source_strength",
        ),
        CheckConstraint(
            "status IN ('extracted','failed')",
            name="ck_news_claims_status",
        ),
        CheckConstraint(
            "source_field IN ('title','summary')",
            name="ck_news_claims_source_field",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.news_articles.id", ondelete="CASCADE"),
        nullable=False,
    )
    claim_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    claim_text: Mapped[str] = mapped_column(Text, nullable=False)
    epistemic_type: Mapped[str] = mapped_column(String(32), nullable=False)
    semantic_domains: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    source_strength: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'unknown'"))
    source_field: Mapped[str] = mapped_column(String(16), nullable=False)
    source_excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    source_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attribution: Mapped[str | None] = mapped_column(Text, nullable=True)
    uncertainty: Mapped[str | None] = mapped_column(Text, nullable=True)
    corrects_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default=text("'extracted'"))
    extractor_version: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class NewsPresentationLocaleUnit(Base):
    """Cached presentation translations for EventPackage fields (never mutates Articles)."""

    __tablename__ = "news_presentation_locale_units"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "package_version",
            "locale",
            "field_key",
            "source_text_hash",
            "translation_engine_version",
            name="uq_news_presentation_locale_identity",
        ),
        Index(
            "ix_news_presentation_locale_lookup",
            "event_id",
            "package_version",
            "locale",
        ),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.news_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    package_version: Mapped[int] = mapped_column(Integer, nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    field_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_text_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    translation_engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    source_text: Mapped[str] = mapped_column(Text, nullable=False)
    translated_text: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class InferenceCallRecordRow(Base):
    """Append-only inference accounting ledger — one row per provider call attempt."""

    __tablename__ = "inference_call_records"
    __table_args__ = (
        Index("ix_inference_call_records_request_id", "request_id"),
        Index("ix_inference_call_records_execution_id", "execution_id"),
        Index("ix_inference_call_records_org_started", "org_id", "started_at"),
        Index("ix_inference_call_records_workspace_started", "workspace_id", "started_at"),
        Index("ix_inference_call_records_provider_model", "provider", "model"),
        CheckConstraint(
            "outcome IN ("
            "'success','error','timeout','client_disconnect','stream_interrupted','rejected')",
            name="ck_inference_call_records_outcome",
        ),
        CheckConstraint(
            "usage_status IN ('exact','estimated','missing')",
            name="ck_inference_call_records_usage_status",
        ),
        CheckConstraint(
            "cost_status IN ('priced','unknown','unpriced','zero')",
            name="ck_inference_call_records_cost_status",
        ),
        {"schema": SCHEMA},
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_id: Mapped[str] = mapped_column(String(64), nullable=False)
    org_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    workspace_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    capability_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pipeline: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    api_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    stream: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    cached_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    reasoning_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    usage_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'missing'"))
    cost_usd: Mapped[float | None] = mapped_column(Numeric(18, 8), nullable=True)
    cost_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'unknown'"))
    pricing_version: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'unknown'"))
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default=text("'USD'"))
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    provider_request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    finish_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_class: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    extras: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class EventUnderstandingRow(Base):
    """Intelligence-layer EventUnderstanding — never mutates News EventPackage rows."""

    __tablename__ = "event_understandings"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "package_version",
            "classifier_version",
            "template_version",
            name="uq_event_understandings_identity",
        ),
        Index(
            "ix_event_understandings_event_package",
            "event_id",
            "package_version",
        ),
        Index("ix_event_understandings_primary_type", "primary_event_type"),
        {"schema": SCHEMA},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(f"{SCHEMA}.news_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    package_version: Mapped[int] = mapped_column(Integer, nullable=False)
    classifier_version: Mapped[str] = mapped_column(String(64), nullable=False)
    template_version: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
