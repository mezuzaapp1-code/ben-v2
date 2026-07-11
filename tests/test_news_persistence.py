"""N1 News persistence: model constraints, migration chain, SQLite uniqueness."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    insert,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database.models import SCHEMA, Base, NewsArticle, NewsSource


def test_news_source_unique_feed_url_constraint():
    unique = next(
        c
        for c in NewsSource.__table__.constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_news_sources_feed_url"
    )
    assert list(unique.columns.keys()) == ["feed_url"]


def test_news_article_unique_source_guid_constraint():
    unique = next(
        c
        for c in NewsArticle.__table__.constraints
        if isinstance(c, UniqueConstraint) and c.name == "uq_news_articles_source_guid"
    )
    assert list(unique.columns.keys()) == ["source_id", "guid"]


def test_news_tables_use_ben_schema():
    assert NewsSource.__table__.schema == SCHEMA
    assert NewsArticle.__table__.schema == SCHEMA


def test_news_article_fk_cascades_to_source():
    fks = list(NewsArticle.__table__.foreign_keys)
    assert len(fks) == 1
    assert fks[0].column.table.name == "news_sources"
    assert fks[0].ondelete == "CASCADE"


def test_news_feed_indexes_present():
    source_idx = {ix.name for ix in NewsSource.__table__.indexes}
    article_idx = {ix.name for ix in NewsArticle.__table__.indexes}
    assert "ix_news_sources_enabled" in source_idx
    assert "ix_news_sources_category" in source_idx
    assert "ix_news_articles_published_id" in article_idx
    assert "ix_news_articles_category_published_id" in article_idx
    assert "ix_news_articles_source_published_id" in article_idx


def test_news_article_has_no_full_body_column():
    columns = set(NewsArticle.__table__.columns.keys())
    assert "body" not in columns
    assert "content" not in columns
    assert "html" not in columns
    assert {"title", "url", "summary", "image_url", "guid", "published_at"} <= columns


def _migration_006_source() -> str:
    path = Path(__file__).resolve().parents[1] / "database" / "migrations" / "versions" / "006_news_v0_1.py"
    return path.read_text(encoding="utf-8")


def test_migration_006_follows_005():
    """Assert revision chain from source — avoids importing alembic at collection time."""
    source = _migration_006_source()
    assert 'revision = "006_news_v0_1"' in source
    assert 'down_revision = "005_project_management_v1"' in source
    assert "def upgrade() -> None:" in source
    assert "def downgrade() -> None:" in source


def test_migration_006_creates_expected_tables_and_constraints():
    """Inspect migration source without applying to a shared database."""
    source = _migration_006_source()
    assert 'create_table(\n        "news_sources"' in source
    assert 'create_table(\n        "news_articles"' in source
    assert "uq_news_sources_feed_url" in source
    assert "uq_news_articles_source_guid" in source
    assert "ix_news_articles_published_id" in source
    assert "ix_news_articles_category_published_id" in source
    assert "ix_news_articles_source_published_id" in source
    assert 'drop_table("news_articles"' in source
    assert 'drop_table("news_sources"' in source
    assert "ENABLE ROW LEVEL SECURITY" not in source


def _sqlite_news_schema(engine) -> None:
    """Portable mirror of News unique constraints for IntegrityError tests."""
    meta = MetaData()
    Table(
        "news_sources",
        meta,
        Column("id", String(36), primary_key=True),
        Column("name", String(256), nullable=False),
        Column("feed_url", String(2048), nullable=False),
        Column("category", String(64), nullable=False),
        Column("enabled", Boolean, nullable=False, default=True),
        Column("language", String(8), nullable=False, default="en"),
        Column("created_at", DateTime(timezone=True), nullable=False),
        Column("updated_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("feed_url", name="uq_news_sources_feed_url"),
    )
    Table(
        "news_articles",
        meta,
        Column("id", String(36), primary_key=True),
        Column("source_id", String(36), ForeignKey("news_sources.id", ondelete="CASCADE"), nullable=False),
        Column("guid", String(1024), nullable=False),
        Column("title", String(1024), nullable=False),
        Column("url", String(2048), nullable=False),
        Column("summary", Text, nullable=True),
        Column("image_url", String(2048), nullable=True),
        Column("published_at", DateTime(timezone=True), nullable=True),
        Column("category", String(64), nullable=False),
        Column("created_at", DateTime(timezone=True), nullable=False),
        UniqueConstraint("source_id", "guid", name="uq_news_articles_source_guid"),
    )
    meta.create_all(engine)


@pytest.fixture
def news_sqlite():
    engine = create_engine("sqlite:///:memory:")
    _sqlite_news_schema(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_duplicate_feed_url_and_guid_rejected_sqlite(news_sqlite: Session):
    now = datetime.now(timezone.utc)
    engine = news_sqlite.get_bind()
    meta = MetaData()
    meta.reflect(bind=engine)
    news_sources = meta.tables["news_sources"]
    news_articles = meta.tables["news_articles"]

    sid = str(uuid.uuid4())
    news_sqlite.execute(
        insert(news_sources).values(
            id=sid,
            name="Reuters World",
            feed_url="https://example.com/world.rss",
            category="world",
            enabled=True,
            language="en",
            created_at=now,
            updated_at=now,
        )
    )
    news_sqlite.commit()

    with pytest.raises(IntegrityError):
        news_sqlite.execute(
            insert(news_sources).values(
                id=str(uuid.uuid4()),
                name="Duplicate",
                feed_url="https://example.com/world.rss",
                category="world",
                enabled=True,
                language="en",
                created_at=now,
                updated_at=now,
            )
        )
        news_sqlite.commit()
    news_sqlite.rollback()

    news_sqlite.execute(
        insert(news_articles).values(
            id=str(uuid.uuid4()),
            source_id=sid,
            guid="guid-1",
            title="Headline",
            url="https://example.com/a1",
            summary=None,
            image_url=None,
            published_at=now,
            category="world",
            created_at=now,
        )
    )
    news_sqlite.commit()

    with pytest.raises(IntegrityError):
        news_sqlite.execute(
            insert(news_articles).values(
                id=str(uuid.uuid4()),
                source_id=sid,
                guid="guid-1",
                title="Same guid",
                url="https://example.com/a1-dup",
                summary=None,
                image_url=None,
                published_at=now,
                category="world",
                created_at=now,
            )
        )
        news_sqlite.commit()
    news_sqlite.rollback()

    # Same guid under a different source is allowed
    sid2 = str(uuid.uuid4())
    news_sqlite.execute(
        insert(news_sources).values(
            id=sid2,
            name="Other Source",
            feed_url="https://example.com/other.rss",
            category="tech",
            enabled=True,
            language="en",
            created_at=now,
            updated_at=now,
        )
    )
    news_sqlite.execute(
        insert(news_articles).values(
            id=str(uuid.uuid4()),
            source_id=sid2,
            guid="guid-1",
            title="Cross-source ok",
            url="https://example.com/other-a1",
            summary="meta",
            image_url=None,
            published_at=now,
            category="tech",
            created_at=now,
        )
    )
    news_sqlite.commit()
    rows = news_sqlite.execute(select(news_articles.c.id)).all()
    assert len(rows) == 2


def test_news_models_registered_on_base_metadata():
    qualified = set(Base.metadata.tables.keys())
    assert f"{SCHEMA}.news_sources" in qualified
    assert f"{SCHEMA}.news_articles" in qualified
