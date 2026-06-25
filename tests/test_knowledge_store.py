"""Hybrid attention and multi-head portable context retrieval."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.knowledge_store import (
    HEAD_CODE,
    HEAD_DOCUMENTATION,
    HEAD_HISTORY,
    build_active_attention_focus,
    build_multi_head_prompt_context,
    format_relative_timestamp,
    insert_context_record,
    query_hybrid_attention,
    resolve_project_db_path,
)
from services.project_tools import projects_root, slugify_project_name


@pytest.fixture
def project_slug(tmp_path, monkeypatch):
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path / "projects"))
    slug = "hybrid-attention-demo"
    return slug


def _iso_days_ago(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()


def test_resolve_project_db_path(project_slug):
    path = resolve_project_db_path(project_slug)
    assert path.name == "project_context.db"
    assert slugify_project_name(project_slug) in path.as_posix()
    assert path.parent == (projects_root() / slugify_project_name(project_slug)).resolve()


def test_hybrid_attention_prefers_fts_entity_match(project_slug):
    insert_context_record(
        project_slug,
        head=HEAD_DOCUMENTATION,
        title="Roadmap",
        content="General planning notes without special entities.",
    )
    insert_context_record(
        project_slug,
        head=HEAD_CODE,
        title="handle_chat signature",
        content="async def handle_chat(message, user_id, tenant_id, tier, *, model_override=None)",
    )
    hits = query_hybrid_attention(project_slug, "handle_chat model_override", limit=3)
    assert hits
    assert hits[0]["title"] == "handle_chat signature"
    assert hits[0]["scores"]["fts"] >= hits[-1]["scores"]["fts"]


def test_recency_boost_elevates_fresh_signature_over_historical_drift(project_slug):
    """Signature drift simulation: stale record vs freshly updated API signature."""
    shared = "async def handle_chat(message, user_id, tenant_id, tier"
    stale_body = (
        f"{shared}, *, thread_id=None, provider_id=None, preferred_language=None)\n"
        "# historical — missing model_override"
    )
    fresh_body = (
        f"{shared}, *, thread_id=None, provider_id=None, "
        "model_override: Optional[str] = None, preferred_language=None)\n"
        "# current production signature"
    )

    stale_id = insert_context_record(
        project_slug,
        head=HEAD_CODE,
        title="handle_chat legacy mock",
        content=stale_body,
        uploaded_at=_iso_days_ago(120),
        updated_at=_iso_days_ago(90),
    )
    fresh_id = insert_context_record(
        project_slug,
        head=HEAD_CODE,
        title="handle_chat current mock",
        content=fresh_body,
        uploaded_at=_iso_days_ago(2),
        updated_at=_iso_days_ago(0),
    )

    hits = query_hybrid_attention(
        project_slug,
        "handle_chat model_override signature mock",
        limit=2,
        head=HEAD_CODE,
    )
    assert len(hits) == 2
    assert hits[0]["id"] == fresh_id
    assert hits[1]["id"] == stale_id
    assert hits[0]["final_score"] > hits[1]["final_score"]
    assert hits[0]["scores"]["recency"] > hits[1]["scores"]["recency"]
    assert "model_override" in hits[0]["content"]


def test_multi_head_prompt_builder_separates_heads(project_slug):
    insert_context_record(
        project_slug,
        head=HEAD_CODE,
        title="test_routes.py",
        content="def test_delete_thread(): ...",
    )
    insert_context_record(
        project_slug,
        head=HEAD_DOCUMENTATION,
        title="Architecture",
        content="Portable storage uses project_context.db per slug.",
    )
    insert_context_record(
        project_slug,
        head=HEAD_HISTORY,
        title="Promoted thread",
        content="User promoted thread alpha-site into portfolio storage.",
    )

    prompt = build_multi_head_prompt_context(project_slug, "portable project_context architecture")
    assert "<portable_project_context>" in prompt
    assert "## Code Head" in prompt
    assert "## Documentation Head" in prompt
    assert "## History Head" in prompt
    assert "test_routes.py" in prompt
    assert "Architecture" in prompt
    assert "Promoted thread" in prompt


def test_format_relative_timestamp_days():
    now = datetime(2026, 6, 6, 12, 0, 0, tzinfo=timezone.utc)
    updated = (now - timedelta(days=2)).replace(microsecond=0).isoformat()
    assert format_relative_timestamp(updated, now=now) == "Updated 2 days ago"


def test_build_active_attention_focus_groups_heads(project_slug):
    insert_context_record(
        project_slug,
        head=HEAD_CODE,
        title="handle_chat",
        content="async def handle_chat(...)",
    )
    insert_context_record(
        project_slug,
        head=HEAD_DOCUMENTATION,
        title="README.md",
        content="Project setup and architecture notes.",
    )
    insert_context_record(
        project_slug,
        head=HEAD_HISTORY,
        title="Thread summary",
        content="Discussed hybrid attention and uploads.",
    )

    payload = build_active_attention_focus(project_slug, "handle_chat architecture")
    assert payload["has_focus"] is True
    assert len(payload["items"]) >= 3
    assert payload["grouped"][HEAD_CODE][0]["entity_name"] == "handle_chat"
    assert payload["grouped"][HEAD_DOCUMENTATION][0]["head_type"] == "Doc"
    assert payload["grouped"][HEAD_HISTORY][0]["head_icon"] == "🕒"
    first = payload["items"][0]
    assert "score_percent" in first
    assert "updated_relative" in first
    assert "semantic_weighted" in first["score_breakdown"]


def test_build_active_attention_focus_empty_without_query(project_slug):
    insert_context_record(
        project_slug,
        head=HEAD_CODE,
        title="unused.py",
        content="pass",
    )
    payload = build_active_attention_focus(project_slug, "   ")
    assert payload["has_focus"] is False
    assert payload["items"] == []
