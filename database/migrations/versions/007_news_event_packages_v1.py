"""BEN News EventPackage v1: news_events + news_event_packages.

Consumer contract storage. Product surfaces read packages only.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

from database.models import SCHEMA

revision = "007_news_event_packages_v1"
down_revision = "006_news_v0_1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_events",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("lifecycle", sa.String(length=32), nullable=False, server_default=sa.text("'open'")),
        sa.Column("headline", sa.String(length=1024), nullable=False),
        sa.Column("happened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "material_updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("current_package_version", sa.Integer(), nullable=False, server_default=sa.text("0")),
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
        schema=SCHEMA,
    )
    op.create_index(
        "ix_news_events_lifecycle_updated",
        "news_events",
        ["lifecycle", "material_updated_at", "id"],
        schema=SCHEMA,
    )

    op.create_table(
        "news_event_packages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_id", UUID(as_uuid=True), nullable=False),
        sa.Column("package_version", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
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
        sa.UniqueConstraint("event_id", "package_version", name="uq_news_event_packages_event_version"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_news_event_packages_event_version",
        "news_event_packages",
        ["event_id", "package_version"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("news_event_packages", schema=SCHEMA)
    op.drop_table("news_events", schema=SCHEMA)
