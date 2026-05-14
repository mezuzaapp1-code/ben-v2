"""Knowledge objects: synthesis type, JSONB content, nullable confidence."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

from database.models import SCHEMA

revision = "002_ko_synthesis_jsonb"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def _drop_ko_type_check(conn: sa.Connection) -> None:
    insp = inspect(conn)
    for ch in insp.get_check_constraints("knowledge_objects", schema=SCHEMA):
        sql = str(ch.get("sqltext", ""))
        if "hypothesis" in sql and "contradiction" in sql and "problem" in sql and "active" not in sql:
            op.drop_constraint(ch["name"], "knowledge_objects", schema=SCHEMA, type_="check")
            return


def upgrade() -> None:
    _drop_ko_type_check(op.get_bind())

    op.create_check_constraint(
        "ck_knowledge_objects_type_inc_synthesis",
        "knowledge_objects",
        "type IN ('problem','hypothesis','insight','decision','contradiction','synthesis')",
        schema=SCHEMA,
    )

    op.alter_column(
        "knowledge_objects",
        "content",
        schema=SCHEMA,
        existing_type=sa.Text(),
        type_=postgresql.JSONB(astext_type=sa.Text()),
        postgresql_using="to_jsonb(content::text)",
        nullable=False,
    )

    op.alter_column(
        "knowledge_objects",
        "confidence",
        schema=SCHEMA,
        existing_type=sa.Numeric(5, 4),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(sa.text(f"DELETE FROM {SCHEMA}.knowledge_objects WHERE type = 'synthesis'"))

    op.drop_constraint("ck_knowledge_objects_type_inc_synthesis", "knowledge_objects", schema=SCHEMA, type_="check")

    op.create_check_constraint(
        "ck_knowledge_objects_type_legacy",
        "knowledge_objects",
        "type IN ('problem','hypothesis','insight','decision','contradiction')",
        schema=SCHEMA,
    )

    op.alter_column(
        "knowledge_objects",
        "content",
        schema=SCHEMA,
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sa.Text(),
        postgresql_using="content::text",
        nullable=False,
    )

    op.execute(sa.text(f"UPDATE {SCHEMA}.knowledge_objects SET confidence = 0 WHERE confidence IS NULL"))

    op.alter_column(
        "knowledge_objects",
        "confidence",
        schema=SCHEMA,
        existing_type=sa.Numeric(5, 4),
        nullable=False,
    )
