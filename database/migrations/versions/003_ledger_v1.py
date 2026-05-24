"""Ledger v1: decisions, approvals, actions with org-scoped RLS.

Downgrade is safe only when all three ledger tables are empty.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database.models import SCHEMA

revision = "003_ledger_v1"
down_revision = "002_ko_synthesis_jsonb"
branch_labels = None
depends_on = None

_LEDGER_TABLES = ("ledger_actions", "ledger_approvals", "ledger_decisions")
_RLS_ORG = "current_setting('app.current_org_id', true)::uuid"


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
    op.create_table(
        "ledger_decisions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("subject", sa.String(length=128), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="draft"),
        sa.Column("policy_snapshot", JSONB(), nullable=True),
        sa.Column("evidence", JSONB(), nullable=True),
        sa.Column("supersedes_decision_id", UUID(as_uuid=True), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('draft','pending','approved','rejected','superseded')",
            name="ck_ledger_decisions_status",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_decision_id"],
            [f"{SCHEMA}.ledger_decisions.id"],
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )

    op.create_index("ix_ledger_decisions_org", "ledger_decisions", ["org_id"], schema=SCHEMA)
    op.create_index(
        "ix_ledger_decisions_org_subject_created",
        "ledger_decisions",
        ["org_id", "subject", "created_at"],
        schema=SCHEMA,
        postgresql_ops={"created_at": "DESC"},
    )
    op.create_index(
        "ix_ledger_decisions_org_status",
        "ledger_decisions",
        ["org_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ledger_decisions_supersedes",
        "ledger_decisions",
        ["supersedes_decision_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("supersedes_decision_id IS NOT NULL"),
    )

    op.create_table(
        "ledger_approvals",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("verdict", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "verdict IN ('approve','reject')",
            name="ck_ledger_approvals_verdict",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            [f"{SCHEMA}.ledger_decisions.id"],
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )

    op.create_index("ix_ledger_approvals_org", "ledger_approvals", ["org_id"], schema=SCHEMA)
    op.create_index(
        "ix_ledger_approvals_decision_created",
        "ledger_approvals",
        ["decision_id", "created_at"],
        schema=SCHEMA,
    )

    op.create_table(
        "ledger_actions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", UUID(as_uuid=True), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("proof_ref", JSONB(), nullable=True),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("reverses_action_id", UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "outcome IN ('done','failed','rolled_back')",
            name="ck_ledger_actions_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id"],
            [f"{SCHEMA}.ledger_decisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reverses_action_id"],
            [f"{SCHEMA}.ledger_actions.id"],
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )

    op.create_index("ix_ledger_actions_org", "ledger_actions", ["org_id"], schema=SCHEMA)
    op.create_index(
        "ix_ledger_actions_decision_created",
        "ledger_actions",
        ["decision_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_ledger_actions_reverses",
        "ledger_actions",
        ["reverses_action_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("reverses_action_id IS NOT NULL"),
    )

    for table in ("ledger_decisions", "ledger_approvals", "ledger_actions"):
        _enable_tenant_rls(table)


def downgrade() -> None:
    # Safe only if ledger_actions, ledger_approvals, and ledger_decisions contain no rows.
    for table in _LEDGER_TABLES:
        _drop_tenant_rls(table)
        op.drop_table(table, schema=SCHEMA)
