"""BEN News E1: news_claim_extractions + news_claims.

Atomic, article-scoped claims with provenance. Does not create Events.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

from database.models import SCHEMA

revision = "008_news_claims_e1"
down_revision = "007_news_event_packages_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "news_claim_extractions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("article_id", UUID(as_uuid=True), nullable=False),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("content_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("claim_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("error_class", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["article_id"],
            [f"{SCHEMA}.news_articles.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "article_id",
            "extractor_version",
            name="uq_news_claim_extractions_article_version",
        ),
        sa.CheckConstraint(
            "status IN ('pending','succeeded','failed','skipped')",
            name="ck_news_claim_extractions_status",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_news_claim_extractions_article_status",
        "news_claim_extractions",
        ["article_id", "status"],
        schema=SCHEMA,
    )

    op.create_table(
        "news_claims",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("article_id", UUID(as_uuid=True), nullable=False),
        sa.Column("claim_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("claim_text", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=32), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("source_field", sa.String(length=16), nullable=False),
        sa.Column("source_excerpt", sa.Text(), nullable=False),
        sa.Column("source_start", sa.Integer(), nullable=True),
        sa.Column("source_end", sa.Integer(), nullable=True),
        sa.Column("attribution", sa.Text(), nullable=True),
        sa.Column("uncertainty", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default=sa.text("'extracted'")),
        sa.Column("extractor_version", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["article_id"],
            [f"{SCHEMA}.news_articles.id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "article_id",
            "extractor_version",
            "claim_fingerprint",
            name="uq_news_claims_article_version_fingerprint",
        ),
        sa.CheckConstraint(
            "claim_type IN ('occurrence','metric','market','implication')",
            name="ck_news_claims_claim_type",
        ),
        sa.CheckConstraint(
            "role IN ('factual','interpretive')",
            name="ck_news_claims_role",
        ),
        sa.CheckConstraint(
            "status IN ('extracted','failed','superseded')",
            name="ck_news_claims_status",
        ),
        sa.CheckConstraint(
            "source_field IN ('title','summary')",
            name="ck_news_claims_source_field",
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_news_claims_article_version",
        "news_claims",
        ["article_id", "extractor_version"],
        schema=SCHEMA,
    )
    op.create_index(
        "ix_news_claims_article_status",
        "news_claims",
        ["article_id", "status"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("news_claims", schema=SCHEMA)
    op.drop_table("news_claim_extractions", schema=SCHEMA)
