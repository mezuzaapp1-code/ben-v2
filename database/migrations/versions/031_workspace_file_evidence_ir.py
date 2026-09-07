"""WRAP sidecar: optional RLS-protected evidence IR JSONB table.

Additive. Does not alter workspace_files, pages, or chunks. Does not bump
EXTRACTION_VERSION. Writes are application-gated by BEN_DOC_EVIDENCE_IR_WRITE.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database.models import SCHEMA

revision = "031_workspace_file_evidence_ir"
down_revision = "030_file_initial_read_jobs"
branch_labels = None
depends_on = None

T = "workspace_file_evidence_ir"


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
        T,
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", UUID(as_uuid=True), nullable=False),
        sa.Column("ir_schema_version", sa.String(length=64), nullable=False),
        sa.Column("extraction_version", sa.Integer(), nullable=False),
        sa.Column("parser_id", sa.String(length=64), nullable=False),
        sa.Column("parser_version", sa.String(length=32), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["file_id"],
            [f"{SCHEMA}.workspace_files.id"],
            ondelete="CASCADE",
            name="fk_workspace_file_evidence_ir_file",
        ),
        sa.UniqueConstraint(
            "file_id",
            "extraction_version",
            "ir_schema_version",
            name="uq_workspace_file_evidence_ir_file_version_schema",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_file_evidence_ir_org_workspace_file",
        T,
        ["org_id", "workspace_id", "file_id"],
        schema=SCHEMA,
    )
    _enable_tenant_rls(T)


def downgrade() -> None:
    op.execute(f"DROP POLICY IF EXISTS {T}_org_isolation ON {SCHEMA}.{T}")
    op.drop_index("ix_workspace_file_evidence_ir_org_workspace_file", table_name=T, schema=SCHEMA)
    op.drop_table(T, schema=SCHEMA)
