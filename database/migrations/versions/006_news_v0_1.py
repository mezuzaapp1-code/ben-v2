"""BEN News v0.1 persistence: news_sources + news_articles (metadata only).

Global system-managed tables (no org RLS). Collector/API out of scope for this revision.
Downgrade drops both tables.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from database.models import SCHEMA

revision = "006_news_v0_1"
down_revision = "005_project_management_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_sources",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("feed_url", sa.String(length=2048), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("language", sa.String(length=8), nullable=False, server_default=sa.text("'en'")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("feed_url", name="uq_news_sources_feed_url"),
        schema=SCHEMA,
    )
    op.create_index("ix_news_sources_enabled", "news_sources", ["enabled"], schema=SCHEMA)
    op.create_index("ix_news_sources_category", "news_sources", ["category"], schema=SCHEMA)

    op.create_table(
        "news_articles",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("source_id", UUID(as_uuid=True), nullable=False),
        sa.Column("guid", sa.String(length=1024), nullable=False),
        sa.Column("title", sa.String(length=1024), nullable=False),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("image_url", sa.String(length=2048), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            [f"{SCHEMA}.news_sources.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("source_id", "guid", name="uq_news_articles_source_guid"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_news_articles_published_id",
        "news_articles",
        ["published_at", "id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_news_articles_category_published_id",
        "news_articles",
        ["category", "published_at", "id"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_news_articles_source_published_id",
        "news_articles",
        ["source_id", "published_at", "id"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("news_articles", schema=SCHEMA)
    op.drop_table("news_sources", schema=SCHEMA)
