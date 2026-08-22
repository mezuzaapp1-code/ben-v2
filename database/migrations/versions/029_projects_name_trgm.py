"""Additive GIN trigram index for tenant-scoped project name search.

Browse keyset still uses ix_projects_org_updated_id (028).
Name contains (ILIKE %q%) is accelerated by pg_trgm; org_id remains a
required predicate in application SQL (never search across tenants).
Does not rewrite project rows, Gate 4A, or document-processing jobs.
"""

from alembic import op

from database.models import SCHEMA

revision = "029_projects_name_trgm"
down_revision = "028_projects_org_updated_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        f"""
        CREATE INDEX IF NOT EXISTS ix_projects_name_trgm
            ON {SCHEMA}.projects USING gin (name gin_trgm_ops)
        """
    )


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {SCHEMA}.ix_projects_name_trgm")
