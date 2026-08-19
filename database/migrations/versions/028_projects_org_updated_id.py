"""Keyset index for org-scoped project listing.

Supports GET /api/projects ORDER BY updated_at DESC, id DESC without a
sequential scan of the org's full project set. Does not change tenancy,
Gate 4A, or document-processing jobs.
"""

from alembic import op

from database.models import SCHEMA

revision = "028_projects_org_updated_id"
down_revision = "027_runner_eligible_jobs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_projects_org_updated_id
            ON {SCHEMA}.projects (org_id, updated_at DESC, id DESC)
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_projects_org_updated_id")
