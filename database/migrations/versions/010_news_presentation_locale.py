"""News presentation locale cache for EventPackage field translations."""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from database.models import SCHEMA

revision = "010_news_presentation_locale"
down_revision = "009_inference_call_records"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_presentation_locale_units",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("package_version", sa.Integer(), nullable=False),
        sa.Column("locale", sa.String(length=16), nullable=False),
        sa.Column("field_key", sa.String(length=64), nullable=False),
        sa.Column("source_text_hash", sa.String(length=64), nullable=False),
        sa.Column("translation_engine_version", sa.String(length=64), nullable=False),
        sa.Column("source_text", sa.Text(), nullable=False),
        sa.Column("translated_text", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "event_id",
            "package_version",
            "locale",
            "field_key",
            "source_text_hash",
            "translation_engine_version",
            name="uq_news_presentation_locale_identity",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"],
            [f"{SCHEMA}.news_events.id"],
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_news_presentation_locale_lookup",
        "news_presentation_locale_units",
        ["event_id", "package_version", "locale"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_news_presentation_locale_lookup",
        table_name="news_presentation_locale_units",
        schema=SCHEMA,
    )
    op.drop_table("news_presentation_locale_units", schema=SCHEMA)
