import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Numeric, String, Text, func, text
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
