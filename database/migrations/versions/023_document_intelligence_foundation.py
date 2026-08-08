"""Document Intelligence Foundation (Gate 1) — WorkspaceFile lifecycle fields,
WorkspaceFilePage (page-level coverage truth), WorkspaceFileChunk (persistent
lexical index unit). Additive, backward compatible, non-destructive. No backfill,
no extraction, no chunk generation, no model/provider calls.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID

from database.models import SCHEMA

revision = "023_doc_intel_foundation"
down_revision = "022_ws_files_v1"
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
    # --- 1. WorkspaceFile lifecycle fields (safe server defaults for existing rows) ---
    op.add_column(
        "workspace_files",
        sa.Column("extraction_status", sa.String(length=32), nullable=False, server_default="pending"),
        schema=SCHEMA,
    )
    op.add_column(
        "workspace_files",
        sa.Column("index_status", sa.String(length=32), nullable=False, server_default="not_indexed"),
        schema=SCHEMA,
    )
    op.add_column("workspace_files", sa.Column("page_count", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column(
        "workspace_files",
        sa.Column("extraction_truncated", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        schema=SCHEMA,
    )
    op.add_column("workspace_files", sa.Column("extraction_version", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column("workspace_files", sa.Column("chunking_version", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column("workspace_files", sa.Column("indexing_version", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column("workspace_files", sa.Column("indexed_chunk_count", sa.Integer(), nullable=True), schema=SCHEMA)
    op.add_column(
        "workspace_files",
        sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.add_column("workspace_files", sa.Column("processing_error", sa.Text(), nullable=True), schema=SCHEMA)
    op.create_check_constraint(
        "ck_workspace_files_extraction_status",
        "workspace_files",
        "extraction_status IN ('pending','extracting','complete','partial','failed')",
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_workspace_files_index_status",
        "workspace_files",
        "index_status IN ('not_indexed','indexing','indexed','stale','failed')",
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_files_workspace_index_status",
        "workspace_files",
        ["workspace_id", "index_status"],
        schema=SCHEMA,
    )

    # --- 2. WorkspaceFilePage: one authoritative record per detected source page ---
    op.create_table(
        "workspace_file_pages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", UUID(as_uuid=True), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("extraction_status", sa.String(length=32), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("needs_ocr", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("extraction_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["file_id"],
            [f"{SCHEMA}.workspace_files.id"],
            ondelete="CASCADE",
            name="fk_workspace_file_pages_file",
        ),
        sa.CheckConstraint(
            "extraction_status IN ('extracted','empty','needs_ocr','failed','skipped')",
            name="ck_workspace_file_pages_extraction_status",
        ),
        sa.UniqueConstraint(
            "file_id", "extraction_version", "page_number",
            name="uq_workspace_file_pages_file_version_page",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_file_pages_org_workspace_file",
        "workspace_file_pages",
        ["org_id", "workspace_id", "file_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_file_pages_file_page",
        "workspace_file_pages",
        ["file_id", "page_number"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_file_pages_file_status",
        "workspace_file_pages",
        ["file_id", "extraction_status"],
        schema=SCHEMA,
    )
    _enable_tenant_rls("workspace_file_pages")

    # --- 3. WorkspaceFileChunk: persistent lexical-index unit ---
    op.create_table(
        "workspace_file_chunks",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("workspace_id", UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", UUID(as_uuid=True), nullable=False),
        sa.Column("page_id", UUID(as_uuid=True), nullable=True),
        sa.Column("page_number", sa.Integer(), nullable=True),
        sa.Column("page_chunk_index", sa.Integer(), nullable=True),
        sa.Column("document_chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("char_count", sa.Integer(), nullable=False),
        sa.Column("extraction_version", sa.Integer(), nullable=False),
        sa.Column("chunking_version", sa.Integer(), nullable=False),
        sa.Column(
            "text_tsv",
            TSVECTOR(),
            sa.Computed("to_tsvector('simple', text)", persisted=True),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["file_id"],
            [f"{SCHEMA}.workspace_files.id"],
            ondelete="CASCADE",
            name="fk_workspace_file_chunks_file",
        ),
        sa.ForeignKeyConstraint(
            ["page_id"],
            [f"{SCHEMA}.workspace_file_pages.id"],
            ondelete="CASCADE",
            name="fk_workspace_file_chunks_page",
        ),
        sa.UniqueConstraint(
            "file_id", "chunking_version", "document_chunk_index",
            name="uq_workspace_file_chunks_file_version_docidx",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_file_chunks_org_workspace_file",
        "workspace_file_chunks",
        ["org_id", "workspace_id", "file_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_file_chunks_file_page",
        "workspace_file_chunks",
        ["file_id", "page_number", "page_chunk_index"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_file_chunks_page",
        "workspace_file_chunks",
        ["page_id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_workspace_file_chunks_tsv",
        "workspace_file_chunks",
        ["text_tsv"],
        schema=SCHEMA,
        postgresql_using="gin",
    )
    _enable_tenant_rls("workspace_file_chunks")


def downgrade() -> None:
    # Drop chunks first (FK page_id -> pages), then pages, then WorkspaceFile additions.
    op.execute(f"DROP POLICY IF EXISTS workspace_file_chunks_org_isolation ON {SCHEMA}.workspace_file_chunks")
    op.drop_index("ix_workspace_file_chunks_tsv", table_name="workspace_file_chunks", schema=SCHEMA)
    op.drop_index("ix_workspace_file_chunks_page", table_name="workspace_file_chunks", schema=SCHEMA)
    op.drop_index("ix_workspace_file_chunks_file_page", table_name="workspace_file_chunks", schema=SCHEMA)
    op.drop_index("ix_workspace_file_chunks_org_workspace_file", table_name="workspace_file_chunks", schema=SCHEMA)
    op.drop_table("workspace_file_chunks", schema=SCHEMA)

    op.execute(f"DROP POLICY IF EXISTS workspace_file_pages_org_isolation ON {SCHEMA}.workspace_file_pages")
    op.drop_index("ix_workspace_file_pages_file_status", table_name="workspace_file_pages", schema=SCHEMA)
    op.drop_index("ix_workspace_file_pages_file_page", table_name="workspace_file_pages", schema=SCHEMA)
    op.drop_index("ix_workspace_file_pages_org_workspace_file", table_name="workspace_file_pages", schema=SCHEMA)
    op.drop_table("workspace_file_pages", schema=SCHEMA)

    op.drop_index("ix_workspace_files_workspace_index_status", table_name="workspace_files", schema=SCHEMA)
    op.drop_constraint("ck_workspace_files_index_status", "workspace_files", schema=SCHEMA, type_="check")
    op.drop_constraint("ck_workspace_files_extraction_status", "workspace_files", schema=SCHEMA, type_="check")
    for col in (
        "processing_error",
        "indexed_at",
        "indexed_chunk_count",
        "indexing_version",
        "chunking_version",
        "extraction_version",
        "extraction_truncated",
        "page_count",
        "index_status",
        "extraction_status",
    ):
        op.drop_column("workspace_files", col, schema=SCHEMA)
