"""Document Upload Intelligence V1 — thread source_state + initial-read flags.

Postgres threads.source_state is the single canonical conversation-source owner.
SQLite thread_store is not modified. Additive, backward compatible.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from database.models import SCHEMA

revision = "029_doc_upload_intel_v1"
down_revision = "028_projects_org_updated_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "threads",
        sa.Column(
            "source_state",
            JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "workspace_files",
        sa.Column(
            "initial_read_status",
            sa.String(length=32),
            nullable=False,
            server_default="none",
        ),
        schema=SCHEMA,
    )
    op.add_column(
        "workspace_files",
        sa.Column("initial_read_at", sa.DateTime(timezone=True), nullable=True),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_workspace_files_initial_read_status",
        "workspace_files",
        "initial_read_status IN ('none','pending','complete','failed','skipped')",
        schema=SCHEMA,
    )
    # Historical READY/FAILED rows were never queued for Initial Read.
    # Skip them so the inventory poller does not wait forever after deploy.
    op.execute(
        sa.text(
            f"UPDATE {SCHEMA}.workspace_files "
            "SET initial_read_status = 'skipped' "
            "WHERE initial_read_status = 'none' "
            "AND status IN ('ready', 'failed')"
        )
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_workspace_files_initial_read_status",
        "workspace_files",
        schema=SCHEMA,
        type_="check",
    )
    op.drop_column("workspace_files", "initial_read_at", schema=SCHEMA)
    op.drop_column("workspace_files", "initial_read_status", schema=SCHEMA)
    op.drop_column("threads", "source_state", schema=SCHEMA)
