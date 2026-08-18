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
    keyset_after,
    projects_page_bounds,
)
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
    assert "setActiveProjectId(selected.id)" in app
    assert "reconcileActiveProject" in app
    assert "selectActiveProject" in app
    assert "workspaceFileInventory.configure" in app
    assert "workspaceId: persistentReady ? activeProjectId || null : null" in app


def test_active_project_not_derived_from_page1_cache():
    app = Path("frontend/src/App.jsx").read_text()
    helper = Path("frontend/src/lib/activeProject.js").read_text()
    assert "reconcileActiveProject" in helper
    assert "selectActiveProject" in helper
    assert "projectOptions.find((p) => p.id === activeProjectId)" not in app
    overlay = Path("frontend/src/components/ProjectLibraryOverlay.jsx").read_text()
    assert "projectLibraryActiveCopy" in overlay


def test_no_n_plus_one_in_project_library_overlay():
    overlay = Path("frontend/src/components/ProjectLibraryOverlay.jsx").read_text()
    assert "fetchProjects(" in overlay
    assert "items.map" in overlay
    assert "/files" not in overlay
    assert "fetchProject(" not in overlay
    service = Path("services/project_service.py").read_text()
    assert "fetch_file_counts" in service
    assert "for row in page_rows:\n            await" not in service
