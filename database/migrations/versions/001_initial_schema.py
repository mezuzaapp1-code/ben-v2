"""initial schema (ben + RLS via app.current_org_id); clone schema ben per tenant if needed."""

from alembic import op
import sqlalchemy as sa
from database.models import SCHEMA, Base

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None
TAB = "threads messages cognitive_events knowledge_objects relationships".split()


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA IF NOT EXISTS " + SCHEMA))
    Base.metadata.create_all(bind=op.get_bind())
    q = "current_setting('app.current_org_id', true)::uuid"
    for t in TAB:
        op.execute(sa.text(f"ALTER TABLE {SCHEMA}.{t} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"CREATE POLICY tenant_isolation ON {SCHEMA}.{t} FOR ALL USING (org_id = {q}) WITH CHECK (org_id = {q})"))


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
    op.execute(sa.text("DROP SCHEMA IF EXISTS " + SCHEMA + " CASCADE"))
