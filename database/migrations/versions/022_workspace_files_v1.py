"""Workspace File Library V1 — canonical uploaded files."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from database.models import SCHEMA

revision = "022_ws_files_v1"
down_revision = "009_inference_call_records"
branch_labels = None
depends_on = None


def _enable_tenant_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {SCHEMA}.{table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {SCHEMA}.{table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_org_isolation ON {SCHEMA}.{table}
        USING (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        WITH CHECK (org_id = NULLIF(current_setting('app.current_org_id', true), '')::uuid)
        """
    )


def upgrade() -> None:
    op.create_table(
        "workspace_files",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(length=512), nullable=False),
        sa.Column("display_name", sa.String(length=512), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("checksum", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=1024), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="uploaded"),
        sa.Column("uploaded_by", sa.String(length=256), nullable=True),
        sa.Column("source_chat_id", sa.String(length=128), nullable=True),
        sa.Column("project_id", UUID(as_uuid=True), nullable=True),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            [f"{SCHEMA}.projects.id"],
            ondelete="CASCADE",
            name="fk_workspace_files_workspace",
        ),
        sa.CheckConstraint(
            "status IN ('uploaded','queued','processing','ready','failed')",
            name="ck_workspace_files_status",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_files_org_workspace",
        "workspace_files",
        ["org_id", "workspace_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_files_workspace_created",
        "workspace_files",
        ["workspace_id", "created_at"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_files_workspace_status",
        "workspace_files",
        ["workspace_id", "status"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_files_checksum",
        "workspace_files",
        ["checksum"],
        schema=SCHEMA,
    )
    _enable_tenant_rls("workspace_files")


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS workspace_files_org_isolation ON {SCHEMA}.workspace_files")
    op.drop_index("ix_workspace_files_checksum", table_name="workspace_files", schema=SCHEMA)
    op.drop_index("ix_workspace_files_workspace_status", table_name="workspace_files", schema=SCHEMA)
    op.drop_index("ix_workspace_files_workspace_created", table_name="workspace_files", schema=SCHEMA)
    op.drop_index("ix_workspace_files_org_workspace", table_name="workspace_files", schema=SCHEMA)
    op.drop_table("workspace_files", schema=SCHEMA)
