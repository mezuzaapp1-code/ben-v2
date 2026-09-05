"""Project Library V1 — keyset pagination, tenancy, bounded pages."""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from services.project_library import (  # noqa: E402
    _ABSOLUTE_MAX_PAGE,
    build_file_count_stmt,
    build_project_list_stmt,
    clamp_project_page_limit,
    decode_project_cursor,
    encode_project_cursor,
    escape_project_like,
    keyset_after,
    normalize_project_search_query,
    parse_project_uuid_query,
    projects_page_bounds,
)
from auth.tenant_ids import personal_tenant_id  # noqa: E402
from services.workspace_files.chunk_retriever import chunk_retrieval_enabled  # noqa: E402
from tests.helpers_auth import AUTH_HEADER, patch_clerk_user  # noqa: E402

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
ORG = uuid.UUID(ORG_A)
ORG_OTHER = uuid.UUID(ORG_B)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.delenv("BEN_LOCAL_BETA_MODE", raising=False)
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS", raising=False)


def _ts(i: int) -> datetime:
    return datetime(2026, 8, 18, 12, 0, tzinfo=timezone.utc) - timedelta(minutes=i)


def test_unsigned_project_list_remains_401():
    client = TestClient(main.app)
    assert client.get("/api/projects").status_code == 401
    assert client.get("/api/projects?limit=50").status_code == 401
    assert client.get("/api/projects?query=Amazon").status_code == 401
    assert client.get("/api/projects?query=11111111-1111-1111-1111-111111111111").status_code == 401


def test_page_size_is_clamped_to_hard_max(monkeypatch):
    monkeypatch.delenv("BEN_PROJECTS_PAGE_SIZE", raising=False)
    monkeypatch.delenv("BEN_PROJECTS_PAGE_MAX", raising=False)
    default, maximum = projects_page_bounds()
    assert default == 50
    assert maximum == 100
    assert clamp_project_page_limit(1) == 1
    assert clamp_project_page_limit(50) == 50
    assert clamp_project_page_limit(100) == 100
    assert clamp_project_page_limit(5000) == 100
    assert clamp_project_page_limit(0) == 1
    assert clamp_project_page_limit(None) == 50
    monkeypatch.setenv("BEN_PROJECTS_PAGE_MAX", "999999")
    assert clamp_project_page_limit(999999) == _ABSOLUTE_MAX_PAGE


def test_cursor_roundtrip_and_invalid():
    pid = uuid.uuid4()
    ts = datetime(2026, 8, 18, 13, 6, 27, 734765, tzinfo=timezone.utc)
    token = encode_project_cursor(updated_at=ts, project_id=pid)
    out_ts, out_id = decode_project_cursor(token)
    assert out_id == pid
    assert out_ts == ts
    with pytest.raises(HTTPException) as ei:
        decode_project_cursor("not-a-cursor")
    assert ei.value.status_code == 400


def test_keyset_pages_are_deterministic_without_duplicates_or_gaps():
    ids = [uuid.UUID(int=i) for i in range(1, 8)]
    rows = [(_ts(i), ids[i], {"id": str(ids[i]), "n": i}) for i in range(7)]
    # already DESC by updated_at (i=0 newest)
    page1, more1 = keyset_after(rows, cursor_ts=None, cursor_id=None, limit=3)
    assert [p["n"] for p in page1] == [0, 1, 2]
    assert more1 is True
    c_ts, c_id = rows[2][0], rows[2][1]
    page2, more2 = keyset_after(rows, cursor_ts=c_ts, cursor_id=c_id, limit=3)
    assert [p["n"] for p in page2] == [3, 4, 5]
    assert more2 is True
    c2_ts, c2_id = rows[5][0], rows[5][1]
    page3, more3 = keyset_after(rows, cursor_ts=c2_ts, cursor_id=c2_id, limit=3)
    assert [p["n"] for p in page3] == [6]
    assert more3 is False
    seen = [p["id"] for p in page1 + page2 + page3]
    assert seen == [str(i) for i in ids]
    assert len(set(seen)) == 7


def test_list_stmt_is_org_scoped_keyset_without_offset():
    cursor_ts = _ts(1)
    cursor_id = uuid.uuid4()
    stmt = build_project_list_stmt(ORG, limit=50, cursor_ts=cursor_ts, cursor_id=cursor_id)
    compiled = str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "offset" not in compiled
    assert "limit 51" in compiled  # limit+1 look-ahead
    assert str(ORG) in compiled
    assert str(ORG_OTHER) not in compiled
    assert "updated_at" in compiled
    other = str(
        build_project_list_stmt(ORG_OTHER, limit=10).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert str(ORG_OTHER) in other
    assert str(ORG) not in other


def test_file_count_stmt_is_org_and_page_bounded():
    ids = [uuid.uuid4() for _ in range(3)]
    compiled = str(
        build_file_count_stmt(ORG, ids).compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()
    assert str(ORG).lower() in compiled
    assert str(ORG_OTHER).lower() not in compiled
    for pid in ids:
        assert str(pid).lower() in compiled
    assert "count(" in compiled
    assert "group by" in compiled


def test_signed_in_org_a_cannot_see_org_b_projects():
    seen: list[str] = []

    async def list_projects(org_id, **_k):
        seen.append(str(org_id))
        return {
            "items": [{"id": "aaa", "name": "A", "status": "active", "updated_at": None, "file_count": 0}],
            "next_cursor": None,
            "projects": [],
        }

    client = TestClient(main.app)
    with patch("routers.projects.list_projects", side_effect=list_projects):
        with patch_clerk_user("org_a_user", org_id=ORG_A, org_role="org:member"):
            a = client.get("/api/projects?limit=50", headers=AUTH_HEADER)
        with patch_clerk_user("org_b_user", org_id=ORG_B, org_role="org:member"):
            b = client.get("/api/projects?limit=50", headers=AUTH_HEADER)
    assert a.status_code == 200
    assert b.status_code == 200
    assert seen == [ORG_A, ORG_B]


def test_router_forwards_clamped_limit_and_cursor():
    captured: list[tuple] = []

    async def list_projects(org_id, *, limit=None, cursor=None, **_k):
        captured.append((str(org_id), limit, cursor))
        return {
            "items": [{"id": "p1", "name": "One", "status": "active", "updated_at": "t", "file_count": 2}],
            "next_cursor": "abc",
            "limit": limit,
            "projects": [{"id": "p1", "name": "One", "status": "active", "updated_at": "t", "file_count": 2}],
        }

    client = TestClient(main.app)
    with patch("routers.projects.list_projects", side_effect=list_projects), patch_clerk_user(
        "org_user", org_id=ORG_A, org_role="org:member"
    ):
        res = client.get("/api/projects?limit=5000&cursor=tok", headers=AUTH_HEADER)
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body["items"], list)
    assert len(body["items"]) == 1
    assert body["next_cursor"] == "abc"
    assert captured[0][0] == ORG_A
    assert captured[0][1] == clamp_project_page_limit(5000)
    assert captured[0][2] == "tok"


@pytest.mark.asyncio
async def test_invalid_cursor_rejected_before_db():
    with patch("services.project_service.get_db_session") as db:
        from services.project_service import list_projects

        with pytest.raises(HTTPException) as ei:
            await list_projects(ORG, cursor="%%%not-valid%%%")
        assert ei.value.status_code == 400
        db.assert_not_called()


def test_gate4a_remains_off():
    assert chunk_retrieval_enabled(uuid.uuid4()) is False


def test_file_lifecycle_inventory_clear_on_workspace_change_still_present():
    src = Path("frontend/src/lib/workspaceFileInventory.js").read_text()
    assert "scopeChanged" in src
    assert "files = []" in src
    app = Path("frontend/src/App.jsx").read_text()
    assert "bindActiveProject" in app
    assert "reconcileActiveProject" in app
    assert "applyTenantScopeChange" in app
    assert "resolveActiveTenantId" in app
    assert "sessionTenantId" in app
    assert "clearActiveProject" in app
    assert "workspaceFileInventory.configure" in app
    assert "workspaceId: persistentReady ? activeProjectId || null : null" in app


def test_active_project_is_tenant_bound_and_not_derived_from_page1_cache():
    app = Path("frontend/src/App.jsx").read_text()
    helper = Path("frontend/src/lib/activeProject.js").read_text()
    tenant = Path("frontend/src/lib/tenantIdentity.js").read_text()
    assert "reconcileActiveProject" in helper
    assert "bindActiveProject" in helper
    assert "applyTenantScopeChange" in helper
    assert "activeProjectForTenant" in helper
    assert "orgId" in tenant and "userId" in tenant
    assert "projectOptions.find((p) => p.id === activeProjectId)" not in app
    assert "[persistentReady, persistentHeaders, sessionTenantId]" in app
    overlay = Path("frontend/src/components/ProjectLibraryOverlay.jsx").read_text()
    assert "projectLibraryActiveCopy" in overlay
    assert "[open, tenantId, debouncedQuery]" in overlay


def test_signed_in_org_isolation_unchanged():
    """Gate A / org isolation still filters list by JWT org, not frontend membership."""
    seen: list[str] = []

    async def list_projects(org_id, **_k):
        seen.append(str(org_id))
        return {
            "items": [{"id": "aaa", "name": "A", "status": "active", "updated_at": None, "file_count": 0}],
            "next_cursor": None,
            "projects": [],
        }

    client = TestClient(main.app)
    with patch("routers.projects.list_projects", side_effect=list_projects):
        with patch_clerk_user("org_a_user", org_id=ORG_A, org_role="org:member"):
            a = client.get("/api/projects?limit=50", headers=AUTH_HEADER)
        with patch_clerk_user("org_b_user", org_id=ORG_B, org_role="org:member"):
            b = client.get("/api/projects?limit=50", headers=AUTH_HEADER)
    assert a.status_code == 200
    assert b.status_code == 200
    assert seen == [ORG_A, ORG_B]
    assert a.json()["items"][0]["id"] != "from-b"


def test_no_n_plus_one_in_project_library_overlay():
    overlay = Path("frontend/src/components/ProjectLibraryOverlay.jsx").read_text()
    assert "fetchProjects(" in overlay
    assert "items.map" in overlay
    assert "/files" not in overlay
    assert "fetchProject(" not in overlay
    service = Path("services/project_service.py").read_text()
    assert "fetch_file_counts" in service
    assert "for row in page_rows:\n            await" not in service


def test_search_query_normalization_and_like_escape():
    assert normalize_project_search_query(None) is None
    assert normalize_project_search_query("  ") is None
    assert normalize_project_search_query("  Amazon  ") == "Amazon"
    assert parse_project_uuid_query("not-a-uuid") is None
    pid = uuid.uuid4()
    assert parse_project_uuid_query(str(pid)) == pid
    assert escape_project_like("100%") == "100\\%"
    assert escape_project_like("a_b") == "a\\_b"


def test_search_stmt_is_org_scoped_ilike_without_offset():
    stmt = build_project_list_stmt(ORG, limit=50, query="Amazon")
    compiled = str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "offset" not in compiled
    assert "ilike" in compiled
    assert "amazon" in compiled
    assert str(ORG) in compiled
    assert str(ORG_OTHER) not in compiled
    other = str(
        build_project_list_stmt(ORG_OTHER, limit=10, query="Amazon").compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert str(ORG_OTHER) in other
    assert str(ORG) not in other


def test_uuid_search_stays_inside_current_org():
    foreign = uuid.uuid4()
    stmt = build_project_list_stmt(ORG, limit=50, query=str(foreign))
    compiled = str(
        stmt.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert str(ORG) in compiled
    assert str(foreign) in compiled
    assert str(ORG_OTHER) not in compiled
    assert "ilike" in compiled


def test_empty_query_browse_has_no_name_filter():
    compiled = str(
        build_project_list_stmt(ORG, limit=50, query="  ").compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    ).lower()
    assert "ilike" not in compiled
    assert str(ORG) in compiled


def test_signed_in_org_a_cannot_search_org_b():
    seen: list[tuple[str, str | None]] = []

    async def list_projects(org_id, *, query=None, **_k):
        seen.append((str(org_id), query))
        return {"items": [], "next_cursor": None, "projects": []}

    foreign_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    client = TestClient(main.app)
    with patch("routers.projects.list_projects", side_effect=list_projects):
        with patch_clerk_user("org_a_user", org_id=ORG_A, org_role="org:member"):
            name = client.get("/api/projects?query=SecretB", headers=AUTH_HEADER)
            uid = client.get(f"/api/projects?query={foreign_id}", headers=AUTH_HEADER)
        with patch_clerk_user("org_b_user", org_id=ORG_B, org_role="org:member"):
            b = client.get("/api/projects?query=SecretB", headers=AUTH_HEADER)
    assert name.status_code == 200
    assert uid.status_code == 200
    assert b.status_code == 200
    assert seen[0] == (ORG_A, "SecretB")
    assert seen[1] == (ORG_A, foreign_id)
    assert seen[2] == (ORG_B, "SecretB")
    assert all(org != ORG_B for org, q in seen[:2])


def test_personal_tenant_search_does_not_use_org_a():
    seen: list[str] = []
    personal = personal_tenant_id("user_personal")

    async def list_projects(org_id, *, query=None, **_k):
        seen.append(str(org_id))
        return {"items": [], "next_cursor": None, "projects": [], "query": query}

    client = TestClient(main.app)
    with patch("routers.projects.list_projects", side_effect=list_projects), patch_clerk_user(
        "user_personal"
    ):
        res = client.get("/api/projects?query=Amazon", headers=AUTH_HEADER)
    assert res.status_code == 200
    assert seen == [personal]
    assert ORG_A not in seen
    assert ORG_B not in seen


def test_router_forwards_search_query():
    captured: list[tuple] = []

    async def list_projects(org_id, *, limit=None, cursor=None, query=None, **_k):
        captured.append((str(org_id), limit, cursor, query))
        return {
            "items": [],
            "next_cursor": None,
            "limit": limit,
            "projects": [],
        }

    client = TestClient(main.app)
    with patch("routers.projects.list_projects", side_effect=list_projects), patch_clerk_user(
        "org_user", org_id=ORG_A, org_role="org:member"
    ):
        res = client.get("/api/projects?query=Gate+3E&limit=50", headers=AUTH_HEADER)
    assert res.status_code == 200
    assert captured[0][0] == ORG_A
    assert captured[0][3] == "Gate 3E"


def test_search_index_migration_is_additive_trgm():
    mig = Path("database/migrations/versions/029_projects_name_trgm.py").read_text()
    assert "ix_projects_name_trgm" in mig
    assert "gin_trgm_ops" in mig
    assert "CREATE EXTENSION IF NOT EXISTS pg_trgm" in mig
    assert "UPDATE " not in mig.split("def upgrade")[1].split("def downgrade")[0]
    assert "028_projects_org_updated_id" in mig


def test_search_does_not_enable_gate4a_or_n_plus_one():
    overlay = Path("frontend/src/components/ProjectLibraryOverlay.jsx").read_text()
    assert "query: debouncedQuery" in overlay
    assert ".filter(" not in overlay
    assert "fetchProject(" not in overlay
    assert "BEN_WORKSPACE_CHUNK_RETRIEVAL" not in overlay
    api = Path("frontend/src/api/projects.js").read_text()
    assert "params.set('query'" in api
    app = Path("frontend/src/App.jsx").read_text()
    assert "fetchProjects(headers, { limit: PROJECT_LIBRARY_DEFAULT_LIMIT })" in app
    assert "query: debouncedQuery" not in app
