"""Intelligence EventUnderstanding persistence (Phase 1b)."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database.models import SCHEMA

revision = "011_event_understandings"
down_revision = "010_news_presentation_locale"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_understandings",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("package_version", sa.Integer(), nullable=False),
        sa.Column("classifier_version", sa.String(length=64), nullable=False),
        sa.Column("template_version", sa.String(length=64), nullable=False),
        sa.Column("primary_event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            [f"{SCHEMA}.news_events.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "event_id",
            "package_version",
            "classifier_version",
            "template_version",
            name="uq_event_understandings_identity",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_event_understandings_event_package",
        "event_understandings",
        ["event_id", "package_version"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_event_understandings_primary_type",
        "event_understandings",
        ["primary_event_type"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_event_understandings_primary_type",
        table_name="event_understandings",
        schema=SCHEMA,
    )
    op.drop_index(
        "ix_event_understandings_event_package",
        table_name="event_understandings",
        schema=SCHEMA,
    )
    op.drop_table("event_understandings", schema=SCHEMA)
