"""BEN Log v1: append-only reasoning continuity events with org-scoped RLS.

Payload JSONB (optional) holds continuity fields for P2 capture, e.g.:
  unresolved (bool), rejected_paths (list), next_step (str), operational_context (object).

Not workflow state, approval, or governance enforcement.
Downgrade is safe only when ben_log_events is empty.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database.models import SCHEMA

revision = "004_ben_log_events_v1"
down_revision = "003_ledger_v1"
branch_labels = None
depends_on = None

_TABLE = "ben_log_events"
_RLS_ORG = "current_setting('app.current_org_id', true)::uuid"

_EVENT_TYPES = (
    "prompt",
    "response",
    "decision",
    "rejection",
    "unresolved",
    "next_step",
    "context",
    "note",
)
_SOURCES = ("chat", "council", "human", "system")


def _enable_tenant_rls(table: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"CREATE POLICY tenant_isolation ON {SCHEMA}.{table} "
            f"FOR ALL USING (org_id = {_RLS_ORG}) WITH CHECK (org_id = {_RLS_ORG})"
        )
    )


def _drop_tenant_rls(table: str) -> None:
    op.execute(sa.text(f"DROP POLICY IF EXISTS tenant_isolation ON {SCHEMA}.{table}"))


def upgrade() -> None:
    types_sql = ",".join(f"'{t}'" for t in _EVENT_TYPES)
    sources_sql = ",".join(f"'{s}'" for s in _SOURCES)

    op.create_table(
        _TABLE,
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(f"event_type IN ({types_sql})", name="ck_ben_log_events_event_type"),
        sa.CheckConstraint(f"source IN ({sources_sql})", name="ck_ben_log_events_source"),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            [f"{SCHEMA}.threads.id"],
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )

    op.create_index("ix_ben_log_events_org", _TABLE, ["org_id"], schema=SCHEMA)
    op.create_index(
        "ix_ben_log_events_org_thread_created",
        _TABLE,
        ["org_id", "thread_id", "created_at"],
        schema=SCHEMA,
        postgresql_ops={"created_at": "DESC"},
    )
    op.create_index(
        "ix_ben_log_events_org_thread_type",
        _TABLE,
        ["org_id", "thread_id", "event_type"],
        schema=SCHEMA,
    )

    _enable_tenant_rls(_TABLE)


def downgrade() -> None:
    _drop_tenant_rls(_TABLE)
    op.drop_table(_TABLE, schema=SCHEMA)
