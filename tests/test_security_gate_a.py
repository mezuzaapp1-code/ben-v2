"""Security Gate A — unsigned callers cannot share persistent customer state."""
from __future__ import annotations

import io
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from auth.persistent_access import (
    assert_persistent_customer_identity,
    is_persistent_customer_identity,
)
from auth.tenant_binding import TenantContext, build_tenant_context
from auth.tenant_ids import personal_tenant_id
from tests.helpers_auth import AUTH_HEADER, patch_clerk_user

ORG_A = "11111111-1111-1111-1111-111111111111"
ORG_B = "22222222-2222-2222-2222-222222222222"
WS_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
FILE_A = "cccccccc-cccc-cccc-cccc-cccccccccccc"
THREAD_A = "dddddddd-dddd-dddd-dddd-dddddddddddd"
ANON = "00000000-0000-0000-0000-000000000001"
USER_A = "user_clerk_aaa"
USER_B = "user_clerk_bbb"


@pytest.fixture(autouse=True)
def _gate_a_env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", ANON)
    monkeypatch.setenv("REQUIRE_ORG_FOR_SIGNED_IN", "false")
    monkeypatch.delenv("BEN_LOCAL_BETA_MODE", raising=False)


def _anon_ctx() -> TenantContext:
    return build_tenant_context("auth_missing", None, False)


def _personal_ctx(user_id: str) -> TenantContext:
    return build_tenant_context("auth_valid", {"user_id": user_id, "email": "a@b.com"}, True)


def _org_ctx(user_id: str = "org_user") -> TenantContext:
    return build_tenant_context(
        "auth_valid",
        {"user_id": user_id, "email": "o@b.com", "org_id": ORG_A, "org_role": "org:member"},
        True,
    )


def test_helper_rejects_shared_anonymous_identity():
    anon = _anon_ctx()
    assert anon.tenant_id == ANON
    assert anon.tenant_type == "anonymous"
    assert is_persistent_customer_identity(anon) is False
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc:
        assert_persistent_customer_identity(anon)
    assert exc.value.status_code == 401


def test_helper_allows_personal_and_org():
    personal = _personal_ctx(USER_A)
    org = _org_ctx()
    assert personal.tenant_type == "personal"
    assert personal.tenant_id == personal_tenant_id(USER_A)
    assert is_persistent_customer_identity(personal) is True
    assert is_persistent_customer_identity(org) is True
    assert assert_persistent_customer_identity(personal) is personal
    assert assert_persistent_customer_identity(org) is org


def test_unsigned_list_projects_401():
    client = TestClient(main.app)
    assert client.get("/api/projects").status_code == 401


def test_unsigned_create_project_401():
    client = TestClient(main.app)
    assert client.post("/api/projects", json={"name": "Alpha"}).status_code == 401


def test_unsigned_get_project_401():
    client = TestClient(main.app)
    assert client.get(f"/api/projects/{WS_A}").status_code == 401


def test_unsigned_workspace_file_list_401():
    client = TestClient(main.app)
    assert client.get(f"/api/workspaces/{WS_A}/files").status_code == 401


def test_unsigned_workspace_file_upload_401():
    client = TestClient(main.app)
    res = client.post(
        f"/api/workspaces/{WS_A}/files",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert res.status_code == 401


def test_unsigned_workspace_file_download_401():
    client = TestClient(main.app)
    assert client.get(f"/api/workspaces/{WS_A}/files/{FILE_A}").status_code == 401
    assert client.get(f"/api/workspaces/{WS_A}/files/{FILE_A}/content").status_code == 401


def test_unsigned_workspace_file_delete_and_retry_401():
    client = TestClient(main.app)
    assert client.delete(f"/api/workspaces/{WS_A}/files/{FILE_A}").status_code == 401
    assert client.post(f"/api/workspaces/{WS_A}/files/{FILE_A}/retry").status_code == 401


def test_unsigned_project_files_alias_401():
    client = TestClient(main.app)
    assert client.get(f"/api/projects/{WS_A}/files").status_code == 401
    res = client.post(
        f"/api/projects/{WS_A}/files",
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert res.status_code == 401
    assert client.get(f"/api/projects/{WS_A}/files/{FILE_A}/content").status_code == 401


def test_unsigned_active_attention_401():
    client = TestClient(main.app)
    res = client.get(
        f"/api/projects/{WS_A}/threads/{THREAD_A}/active-attention",
        params={"query": "lifecycle"},
    )
    assert res.status_code == 401
    assert res.json().get("detail") == "Unauthorized"


def test_unsigned_thread_list_401():
    client = TestClient(main.app)
    assert client.get("/api/threads").status_code == 401


def test_unsigned_thread_read_create_delete_401():
    client = TestClient(main.app)
    assert client.get(f"/api/threads/{THREAD_A}").status_code == 401
    assert client.delete(f"/api/threads/{THREAD_A}").status_code == 401
    assert client.post("/api/threads/project-workspace", json={}).status_code == 401


def test_unsigned_chat_with_project_id_401_and_no_file_injection():
    injected = {"called": False}

    async def boom(*_a, **_k):
        injected["called"] = True
        raise AssertionError("chat must not run for unsigned project-scoped request")

    with patch.object(main, "stream_chat_response", side_effect=boom), patch.object(
        main, "handle_chat", side_effect=boom
    ):
        client = TestClient(main.app)
        res = client.post(
            "/chat/stream",
            json={"message": "read the file", "tier": "free", "project_id": WS_A},
        )
        res2 = client.post(
            "/chat",
            json={"message": "read the file", "tier": "free", "project_id": WS_A},
        )
    assert res.status_code == 401
    assert res2.status_code == 401
    assert injected["called"] is False


def test_unsigned_council_401():
    called = {"n": False}

    async def boom(*_a, **_k):
        called["n"] = True
        raise AssertionError("unsigned council must not persist")

    with patch.object(main, "run_council", side_effect=boom):
        client = TestClient(main.app)
        assert client.post("/council", json={"question": "q?"}).status_code == 401
        assert client.post("/council/stream", json={"question": "q?"}).status_code == 401
    assert called["n"] is False


def test_unsigned_chat_without_workspace_is_401_not_shared_persist():
    """Ephemeral unsigned chat is not safe today (always persists). Prefer 401."""
    called = {"handle": False, "stream": False}

    async def mark_handle(*_a, **_k):
        called["handle"] = True
        return {"thread_id": ANON, "response": "nope", "model_used": "m", "cost_usd": 0.0}

    async def mark_stream(*_a, **_k):
        called["stream"] = True
        if False:
            yield ""

    with patch.object(main, "handle_chat", side_effect=mark_handle), patch.object(
        main, "stream_chat_response", side_effect=mark_stream
    ):
        client = TestClient(main.app)
        assert client.post("/chat", json={"message": "hi", "tier": "free"}).status_code == 401
        assert client.post("/chat/stream", json={"message": "hi", "tier": "free"}).status_code == 401
    assert called["handle"] is False
    assert called["stream"] is False


def test_two_unsigned_clients_do_not_share_persistent_pool():
    client_a = TestClient(main.app)
    client_b = TestClient(main.app)
    surfaces = [
        client_a.get("/api/projects"),
        client_b.get("/api/projects"),
        client_a.get(f"/api/workspaces/{WS_A}/files"),
        client_b.get(f"/api/workspaces/{WS_A}/files"),
        client_a.get("/api/threads"),
        client_b.get("/api/threads"),
        client_a.post("/chat", json={"message": "hi", "tier": "free", "project_id": WS_A}),
        client_b.post("/chat", json={"message": "hi", "tier": "free", "project_id": WS_A}),
    ]
    assert all(r.status_code == 401 for r in surfaces)
    for r in surfaces:
        body = r.json()
        assert "projects" not in body
        assert "items" not in body
        assert "threads" not in body


def test_health_and_ready_remain_public():
    client = TestClient(main.app)
    health = client.get("/health")
    ready = client.get("/ready")
    assert health.status_code == 200
    assert ready.status_code in {200, 503}
    assert "detail" not in (health.json() or {}) or health.json().get("status")


def test_personal_user_a_cannot_see_personal_user_b_threads_or_files():
    listed_orgs: list[str] = []
    file_orgs: list[str] = []

    async def list_threads(org_id):
        listed_orgs.append(str(org_id))
        return {"threads": [], "request_id": "x"}

    async def list_files(*, org_id, workspace_id, **_k):
        file_orgs.append(str(org_id))
        return {"items": [], "count": 0, "workspace_id": str(workspace_id)}

    with patch.object(main, "list_threads", side_effect=list_threads), patch(
        "routers.workspace_files.file_service.list_files", side_effect=list_files
    ):
        client = TestClient(main.app)
        with patch_clerk_user(USER_A):
            ta = client.get("/api/threads", headers=AUTH_HEADER)
            fa = client.get(f"/api/workspaces/{WS_A}/files", headers=AUTH_HEADER)
        with patch_clerk_user(USER_B):
            tb = client.get("/api/threads", headers=AUTH_HEADER)
            fb = client.get(f"/api/workspaces/{WS_A}/files", headers=AUTH_HEADER)

    assert ta.status_code == 200
    assert tb.status_code == 200
    assert fa.status_code == 200
    assert fb.status_code == 200
    assert listed_orgs[0] == personal_tenant_id(USER_A)
    assert listed_orgs[1] == personal_tenant_id(USER_B)
    assert listed_orgs[0] != listed_orgs[1]
    assert file_orgs[0] == personal_tenant_id(USER_A)
    assert file_orgs[1] == personal_tenant_id(USER_B)
    assert file_orgs[0] != file_orgs[1]


def test_personal_signed_in_upload_and_chat_still_work():
    async def fake_upload(**kwargs):
        return {
            "id": FILE_A,
            "organization_id": str(kwargs["org_id"]),
            "workspace_id": str(kwargs["workspace_id"]),
            "status": "ready",
        }

    captured: dict[str, str] = {}

    async def fake_chat(message, user_id, tenant_id, tier, **_k):
        captured["tenant_id"] = tenant_id
        captured["user_id"] = user_id
        return {"thread_id": THREAD_A, "response": "ok", "model_used": "m", "cost_usd": 0.0}

    with patch(
        "routers.workspace_files.file_service.upload_file", side_effect=fake_upload
    ), patch.object(main, "handle_chat", side_effect=fake_chat), patch_clerk_user(USER_A):
        client = TestClient(main.app)
        up = client.post(
            f"/api/workspaces/{WS_A}/files",
            headers=AUTH_HEADER,
            files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
        )
        chat = client.post(
            "/chat",
            json={"message": "hi", "tier": "free", "project_id": WS_A},
            headers=AUTH_HEADER,
        )
    assert up.status_code == 200
    assert up.json()["organization_id"] == personal_tenant_id(USER_A)
    assert chat.status_code == 200
    assert captured["tenant_id"] == personal_tenant_id(USER_A)
    assert captured["user_id"] == USER_A


def test_organization_signed_in_project_files_chat_still_work():
    created: list[str] = []

    async def fake_create(org_id, **kwargs):
        created.append(str(org_id))
        return {"id": str(uuid.uuid4()), "org_id": str(org_id), "name": kwargs["name"]}

    async def fake_list(org_id, **_k):
        return {"projects": [{"id": WS_A, "org_id": str(org_id), "name": "W"}]}

    async def fake_files(*, org_id, workspace_id, **_k):
        return {"items": [], "count": 0, "workspace_id": str(workspace_id)}

    captured: dict[str, str] = {}

    async def fake_chat(message, user_id, tenant_id, tier, **_k):
        captured["tenant_id"] = tenant_id
        return {"thread_id": THREAD_A, "response": "ok", "model_used": "m", "cost_usd": 0.0}

    with patch_clerk_user("org_user", org_id=ORG_A, org_role="org:admin"), patch(
        "routers.projects.create_project", side_effect=fake_create
    ), patch(
        "routers.projects.initialize_project_setup", new_callable=AsyncMock
    ), patch(
        "routers.projects.list_projects", side_effect=fake_list
    ), patch(
        "routers.workspace_files.file_service.list_files", side_effect=fake_files
    ), patch.object(main, "handle_chat", side_effect=fake_chat):
        client = TestClient(main.app)
        listed = client.get("/api/projects", headers=AUTH_HEADER)
        created_res = client.post("/api/projects", json={"name": "Org WS"}, headers=AUTH_HEADER)
        files = client.get(f"/api/workspaces/{WS_A}/files", headers=AUTH_HEADER)
        chat = client.post(
            "/chat",
            json={"message": "hi", "tier": "free", "project_id": WS_A},
            headers=AUTH_HEADER,
        )
    assert listed.status_code == 200
    assert created_res.status_code == 200
    assert files.status_code == 200
    assert chat.status_code == 200
    assert created == [ORG_A]
    assert captured["tenant_id"] == ORG_A


def test_organization_b_cannot_see_organization_a_scope():
    seen: list[str] = []

    async def list_projects(org_id, **_k):
        seen.append(f"projects:{org_id}")
        return {"projects": []}

    async def list_files(*, org_id, workspace_id, **_k):
        seen.append(f"files:{org_id}")
        return {"items": [], "count": 0, "workspace_id": str(workspace_id)}

    async def list_threads(org_id):
        seen.append(f"threads:{org_id}")
        return {"threads": []}

    client = TestClient(main.app)
    with patch("routers.projects.list_projects", side_effect=list_projects), patch(
        "routers.workspace_files.file_service.list_files", side_effect=list_files
    ), patch.object(main, "list_threads", side_effect=list_threads):
        with patch_clerk_user("org_a_user", org_id=ORG_A, org_role="org:member"):
            a_p = client.get("/api/projects", headers=AUTH_HEADER)
            a_f = client.get(f"/api/workspaces/{WS_A}/files", headers=AUTH_HEADER)
            a_t = client.get("/api/threads", headers=AUTH_HEADER)
        with patch_clerk_user("org_b_user", org_id=ORG_B, org_role="org:member"):
            b_p = client.get("/api/projects", headers=AUTH_HEADER)
            b_f = client.get(f"/api/workspaces/{WS_A}/files", headers=AUTH_HEADER)
            b_t = client.get("/api/threads", headers=AUTH_HEADER)

    assert {a_p.status_code, a_f.status_code, a_t.status_code} == {200}
    assert {b_p.status_code, b_f.status_code, b_t.status_code} == {200}
    assert seen == [
        f"projects:{ORG_A}",
        f"files:{ORG_A}",
        f"threads:{ORG_A}",
        f"projects:{ORG_B}",
        f"files:{ORG_B}",
        f"threads:{ORG_B}",
    ]
    assert ORG_A != ORG_B


def test_doc_processing_cron_auth_unchanged():
    """Gate A must not convert the drain path into customer-JWT or anonymous tenant auth."""
    drained = {"n": 0}

    async def fake_drain(*, worker_id, limit):
        drained["n"] += 1
        return {"claimed": 0, "completed": 0, "requeued": 0, "failed": 0, "worker_id": worker_id}

    client = TestClient(main.app)
    with patch(
        "routers.document_processing.drain_document_processing_jobs",
        side_effect=fake_drain,
    ):
        missing = client.post("/api/internal/documents/processing/drain")
        assert missing.status_code == 503
        assert drained["n"] == 0

        with patch.dict("os.environ", {"BEN_DOC_PROCESSING_CRON_SECRET": "cron-secret"}):
            bad = client.post(
                "/api/internal/documents/processing/drain",
                headers={"X-BEN-Doc-Processing-Cron-Secret": "wrong"},
            )
            assert bad.status_code == 401
            assert drained["n"] == 0

            ok = client.post(
                "/api/internal/documents/processing/drain",
                headers={"X-BEN-Doc-Processing-Cron-Secret": "cron-secret"},
            )
    assert ok.status_code == 200
    assert drained["n"] == 1


def test_scoped_drain_requires_cron_secret_and_does_not_call_generic():
    """File-id drain is the same cron-secret gate; it never falls back to generic claim."""
    scoped = {"ids": []}
    generic = {"n": 0}
    fid = uuid.uuid4()

    async def fake_scoped(file_id, *, worker_id):
        scoped["ids"].append(file_id)
        return {
            "claimed": 0, "file_id": str(file_id), "outcome": "no_eligible_job",
            "worker_id": worker_id,
        }

    async def fake_generic(**_k):
        generic["n"] += 1
        return {"claimed": 0}

    path = f"/api/internal/documents/processing/files/{fid}/drain"
    client = TestClient(main.app)
    with patch(
        "routers.document_processing.drain_document_processing_job_for_file",
        side_effect=fake_scoped,
    ), patch(
        "routers.document_processing.drain_document_processing_jobs",
        side_effect=fake_generic,
    ):
        missing = client.post(path)
        assert missing.status_code == 503
        assert scoped["ids"] == [] and generic["n"] == 0

        with patch.dict("os.environ", {"BEN_DOC_PROCESSING_CRON_SECRET": "cron-secret"}):
            bad = client.post(path, headers={"X-BEN-Doc-Processing-Cron-Secret": "wrong"})
            assert bad.status_code == 401
            assert scoped["ids"] == [] and generic["n"] == 0

            ok = client.post(path, headers={"X-BEN-Doc-Processing-Cron-Secret": "cron-secret"})
            assert ok.status_code == 200
            assert ok.json()["file_id"] == str(fid)
            assert ok.json()["outcome"] == "no_eligible_job"
            assert scoped["ids"] == [fid]
            assert generic["n"] == 0

            invalid = client.post(
                "/api/internal/documents/processing/files/not-a-uuid/drain",
                headers={"X-BEN-Doc-Processing-Cron-Secret": "cron-secret"},
            )
            assert invalid.status_code == 422
            assert generic["n"] == 0


def test_runner_drain_and_stats_require_cron_secret_and_do_not_call_generic():
    runner = {"n": 0}
    stats = {"n": 0}
    generic = {"n": 0}

    async def fake_runner(*, worker_id, limit):
        runner["n"] += 1
        return {"claimed": 0, "claim_policy": "disabled", "worker_id": worker_id}

    async def fake_stats():
        stats["n"] += 1
        return {"due_queue_depth": 0, "claim_policy": "disabled"}

    async def fake_generic(**_k):
        generic["n"] += 1
        return {"claimed": 0}

    client = TestClient(main.app)
    with patch(
        "routers.document_processing.drain_document_processing_jobs_for_runner",
        side_effect=fake_runner,
    ), patch(
        "routers.document_processing.runner_processing_stats",
        side_effect=fake_stats,
    ), patch(
        "routers.document_processing.drain_document_processing_jobs",
        side_effect=fake_generic,
    ):
        drain_path = "/api/internal/documents/processing/runner/drain"
        stats_path = "/api/internal/documents/processing/runner/stats"
        assert client.post(drain_path).status_code == 503
        assert client.get(stats_path).status_code == 503
        assert runner["n"] == 0 and stats["n"] == 0 and generic["n"] == 0

        with patch.dict("os.environ", {"BEN_DOC_PROCESSING_CRON_SECRET": "cron-secret"}):
            assert client.post(
                drain_path, headers={"X-BEN-Doc-Processing-Cron-Secret": "wrong"},
            ).status_code == 401
            assert client.get(
                stats_path, headers={"X-BEN-Doc-Processing-Cron-Secret": "wrong"},
            ).status_code == 401
            assert runner["n"] == 0 and stats["n"] == 0 and generic["n"] == 0

            ok_drain = client.post(
                drain_path, headers={"X-BEN-Doc-Processing-Cron-Secret": "cron-secret"},
            )
            ok_stats = client.get(
                stats_path, headers={"X-BEN-Doc-Processing-Cron-Secret": "cron-secret"},
            )
            assert ok_drain.status_code == 200
            assert ok_drain.json()["claim_policy"] == "disabled"
            assert ok_stats.status_code == 200
            assert runner["n"] == 1 and stats["n"] == 1
            assert generic["n"] == 0


def test_initial_read_drain_requires_cron_secret_and_does_not_call_extraction():
    """Dedicated IR drain is the same cron-secret gate; never extraction."""
    ir = {"n": 0}
    extract = {"n": 0}

    async def fake_ir(*, worker_id, limit):
        ir["n"] += 1
        return {"claimed": 0, "worker_id": worker_id}

    async def fake_extract(**_k):
        extract["n"] += 1
        return {"claimed": 0}

    client = TestClient(main.app)
    path = "/api/internal/documents/processing/initial-read/drain"
    with patch(
        "routers.document_processing.drain_file_initial_reads",
        side_effect=fake_ir,
    ), patch(
        "routers.document_processing.drain_document_processing_jobs",
        side_effect=fake_extract,
    ), patch(
        "routers.document_processing.drain_document_processing_jobs_for_runner",
        side_effect=fake_extract,
    ):
        assert client.post(path).status_code == 503
        assert ir["n"] == 0 and extract["n"] == 0
        with patch.dict("os.environ", {"BEN_DOC_PROCESSING_CRON_SECRET": "cron-secret"}):
            assert client.post(
                path, headers={"X-BEN-Doc-Processing-Cron-Secret": "wrong"},
            ).status_code == 401
            ok = client.post(
                path, headers={"X-BEN-Doc-Processing-Cron-Secret": "cron-secret"},
            )
            assert ok.status_code == 200
            assert ir["n"] == 1
            assert extract["n"] == 0


def test_old_anonymous_fallback_no_longer_lists_shared_projects():
    """Phase 1F — old leak was unsigned GET /api/projects → BEN_ANONYMOUS_ORG_ID list.

    New behavior: 401 and list_projects is never invoked for the shared anonymous tenant.
    """
    called = {"orgs": []}

    async def capture_list(org_id, **_k):
        called["orgs"].append(str(org_id))
        return {"projects": [{"id": WS_A, "name": "Local Files Workspace"}]}

    with patch("routers.projects.list_projects", side_effect=capture_list):
        client = TestClient(main.app)
        res = client.get("/api/projects")
    assert res.status_code == 401
    assert called["orgs"] == []
    assert "projects" not in res.json()


def test_personal_does_not_require_organization():
    with patch_clerk_user(USER_A), patch.object(
        main, "list_threads", new_callable=AsyncMock, return_value={"threads": []}
    ) as listed:
        client = TestClient(main.app)
        res = client.get("/api/threads", headers=AUTH_HEADER)
    assert res.status_code == 200
    assert str(listed.await_args[0][0]) == personal_tenant_id(USER_A)
