"""Project management v1: projects, members, tasks, financial_ledger with org-scoped RLS.

Downgrade is safe only when all four tables are empty.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from database.models import SCHEMA

revision = "005_project_management_v1"
down_revision = "004_ben_log_events_v1"
branch_labels = None
depends_on = None

_RLS_ORG = "current_setting('app.current_org_id', true)::uuid"

_PROJECT_STATUSES = ("active", "on_hold", "completed", "archived")
_MEMBER_TYPES = ("EMPLOYEE", "VENDOR")
_TASK_STATUSES = ("todo", "in_progress", "blocked", "done")
_TASK_PRIORITIES = ("low", "medium", "high", "urgent")
_LEDGER_ENTRY_TYPES = ("INCOME", "EXPENSE")
_LEDGER_STATUSES = ("pending", "recorded", "paid", "cancelled")

_TABLES = ("financial_ledger", "project_tasks", "project_members", "projects")


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
    project_status_sql = ",".join(f"'{s}'" for s in _PROJECT_STATUSES)
    member_type_sql = ",".join(f"'{t}'" for t in _MEMBER_TYPES)
    task_status_sql = ",".join(f"'{s}'" for s in _TASK_STATUSES)
    task_priority_sql = ",".join(f"'{p}'" for p in _TASK_PRIORITIES)
    ledger_type_sql = ",".join(f"'{t}'" for t in _LEDGER_ENTRY_TYPES)
    ledger_status_sql = ",".join(f"'{s}'" for s in _LEDGER_STATUSES)

    op.create_table(
        "projects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(f"status IN ({project_status_sql})", name="ck_projects_status"),
        schema=SCHEMA,
    )
    op.create_index("ix_projects_org", "projects", ["org_id"], schema=SCHEMA)
    op.create_index("ix_projects_org_status", "projects", ["org_id", "status"], schema=SCHEMA)
    op.create_index("ix_projects_created", "projects", ["created_at"], schema=SCHEMA)
    _enable_tenant_rls("projects")

    op.create_table(
        "project_members",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("member_type", sa.String(length=16), nullable=False),
        sa.Column("role", sa.String(length=128), nullable=True),
        sa.Column("hourly_rate", sa.Numeric(12, 2), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("contact_notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(f"member_type IN ({member_type_sql})", name="ck_project_members_member_type"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            [f"{SCHEMA}.projects.id"],
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_project_members_org", "project_members", ["org_id"], schema=SCHEMA)
    op.create_index("ix_project_members_project", "project_members", ["project_id"], schema=SCHEMA)
    op.create_index("ix_project_members_org_project", "project_members", ["org_id", "project_id"], schema=SCHEMA)
    _enable_tenant_rls("project_members")

    op.create_table(
        "project_tasks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="todo"),
        sa.Column("priority", sa.String(length=16), nullable=False, server_default="medium"),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("assigned_to", UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(f"status IN ({task_status_sql})", name="ck_project_tasks_status"),
        sa.CheckConstraint(f"priority IN ({task_priority_sql})", name="ck_project_tasks_priority"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            [f"{SCHEMA}.projects.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_to"],
            [f"{SCHEMA}.project_members.id"],
            ondelete="SET NULL",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_project_tasks_org", "project_tasks", ["org_id"], schema=SCHEMA)
    op.create_index("ix_project_tasks_project", "project_tasks", ["project_id"], schema=SCHEMA)
    op.create_index("ix_project_tasks_org_project", "project_tasks", ["org_id", "project_id"], schema=SCHEMA)
    op.create_index("ix_project_tasks_assigned_to", "project_tasks", ["assigned_to"], schema=SCHEMA)
    op.create_index("ix_project_tasks_due_date", "project_tasks", ["due_date"], schema=SCHEMA)
    _enable_tenant_rls("project_tasks")

    op.create_table(
        "financial_ledger",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("project_id", UUID(as_uuid=True), nullable=False),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("entry_type", sa.String(length=16), nullable=False),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="USD"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(f"entry_type IN ({ledger_type_sql})", name="ck_financial_ledger_entry_type"),
        sa.CheckConstraint(f"status IN ({ledger_status_sql})", name="ck_financial_ledger_status"),
        sa.ForeignKeyConstraint(
            ["project_id"],
            [f"{SCHEMA}.projects.id"],
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_financial_ledger_org", "financial_ledger", ["org_id"], schema=SCHEMA)
    op.create_index("ix_financial_ledger_project", "financial_ledger", ["project_id"], schema=SCHEMA)
    op.create_index("ix_financial_ledger_org_project", "financial_ledger", ["org_id", "project_id"], schema=SCHEMA)
    op.create_index("ix_financial_ledger_due_date", "financial_ledger", ["due_date"], schema=SCHEMA)
    _enable_tenant_rls("financial_ledger")


def downgrade() -> None:
    for table in _TABLES:
        _drop_tenant_rls(table)
        op.drop_table(table, schema=SCHEMA)
