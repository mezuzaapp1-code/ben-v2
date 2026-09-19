"""initial schema (ben + RLS via app.current_org_id); clone schema ben per tenant if needed.

Revision-local DDL only. Does not use live ORM metadata.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None

# Frozen locally. Do not import database.models.SCHEMA or Base.
SCHEMA = "ben"
TAB = ("threads", "messages", "cognitive_events", "knowledge_objects", "relationships")
_RLS_ORG = "current_setting('app.current_org_id', true)::uuid"

# Secondary indexes (explicit names from the current five-table ORM Index()
# declarations). PostgreSQL still creates <table>_pkey for primary keys.
_INDEXES = (
    ("ix_threads_org", "threads", ["org_id"]),
    ("ix_threads_created", "threads", ["created_at"]),
    ("ix_messages_org", "messages", ["org_id"]),
    ("ix_messages_thread", "messages", ["thread_id"]),
    ("ix_messages_created", "messages", ["created_at"]),
    ("ix_ce_org", "cognitive_events", ["org_id"]),
    ("ix_ce_thread", "cognitive_events", ["thread_id"]),
    ("ix_ce_created", "cognitive_events", ["created_at"]),
    ("ix_ko_org", "knowledge_objects", ["org_id"]),
    ("ix_ko_created", "knowledge_objects", ["created_at"]),
    ("ix_ko_updated", "knowledge_objects", ["updated_at"]),
    ("ix_rel_org", "relationships", ["org_id"]),
    ("ix_rel_src", "relationships", ["source_object_id"]),
    ("ix_rel_tgt", "relationships", ["target_object_id"]),
    ("ix_rel_created", "relationships", ["created_at"]),
)


def upgrade() -> None:
    op.execute(sa.text("CREATE SCHEMA " + SCHEMA))

    op.create_table(
        "threads",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        schema=SCHEMA,
    )

    op.create_table(
        "messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            [f"{SCHEMA}.threads.id"],
            ondelete="CASCADE",
            name="fk_messages_thread_id",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "cognitive_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("thread_id", UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "type IN ('challenge_raised','contradiction_found','insight_discovered',"
            "'decision_made','assumption_rejected')",
            name="ck_cognitive_events_type",
        ),
        sa.ForeignKeyConstraint(
            ["thread_id"],
            [f"{SCHEMA}.threads.id"],
            ondelete="CASCADE",
            name="fk_cognitive_events_thread_id",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "knowledge_objects",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        # Name is revision-local. 002 discovers this CHECK by SQL text, not name,
        # then replaces it with ck_knowledge_objects_type_inc_synthesis.
        sa.CheckConstraint(
            "type IN ('problem','hypothesis','insight','decision','contradiction')",
            name="ck_knowledge_objects_type",
        ),
        sa.CheckConstraint(
            "status IN ('active','evolving','resolved','rejected','archived')",
            name="ck_knowledge_objects_status",
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "relationships",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("org_id", UUID(as_uuid=True), nullable=False),
        sa.Column("source_object_id", UUID(as_uuid=True), nullable=False),
        sa.Column("relation", sa.String(length=64), nullable=False),
        sa.Column("target_object_id", UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "relation IN ('contradicts','supports','evolved_from','challenges','resolves','depends_on')",
            name="ck_relationships_relation",
        ),
        sa.ForeignKeyConstraint(
            ["source_object_id"],
            [f"{SCHEMA}.knowledge_objects.id"],
            ondelete="CASCADE",
            name="fk_relationships_source_object_id",
        ),
        sa.ForeignKeyConstraint(
            ["target_object_id"],
            [f"{SCHEMA}.knowledge_objects.id"],
            ondelete="CASCADE",
            name="fk_relationships_target_object_id",
        ),
        schema=SCHEMA,
    )

    for name, table, cols in _INDEXES:
        op.create_index(name, table, cols, schema=SCHEMA)

    for t in TAB:
        op.execute(sa.text(f"ALTER TABLE {SCHEMA}.{t} ENABLE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY tenant_isolation ON {SCHEMA}.{t} "
                f"FOR ALL USING (org_id = {_RLS_ORG}) WITH CHECK (org_id = {_RLS_ORG})"
            )
        )


def downgrade() -> None:
    for t in reversed(TAB):
        op.execute(sa.text(f"DROP POLICY tenant_isolation ON {SCHEMA}.{t}"))

    for name, table, _cols in reversed(_INDEXES):
        op.drop_index(name, table_name=table, schema=SCHEMA)

    op.drop_table("relationships", schema=SCHEMA)
    op.drop_table("messages", schema=SCHEMA)
    op.drop_table("cognitive_events", schema=SCHEMA)
    op.drop_table("knowledge_objects", schema=SCHEMA)
    op.drop_table("threads", schema=SCHEMA)
    op.execute(sa.text("DROP SCHEMA " + SCHEMA))
