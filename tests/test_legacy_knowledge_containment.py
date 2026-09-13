"""Gate 0 — Legacy Knowledge Containment.

Disable-without-deletion: HTTP and prompt paths deny access; stored rows remain.
"""
from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

from database.knowledge_store import get_connection, init_knowledge_store  # noqa: E402
from services.knowledge_containment import (  # noqa: E402
    LEGACY_KNOWLEDGE_CONTAINED_CODE,
    LEGACY_KNOWLEDGE_CONTAINED_MESSAGE,
    LEGACY_KNOWLEDGE_CONTAINED_STATUS,
    contained_few_shot_block,
    contained_portable_project_context,
)
from services.knowledge_injection import inject_knowledge_few_shot  # noqa: E402
from services.knowledge_service import (  # noqa: E402
    add_knowledge_document,
    build_knowledge_few_shot_block,
    create_knowledge_base,
    list_knowledge_bases,
    list_knowledge_documents,
)
from services.knowledge_store import (  # noqa: E402
    HEAD_CODE,
    build_multi_head_prompt_context,
    insert_context_record,
    list_knowledge_files,
    resolve_project_knowledge_dir,
    stream_knowledge_upload,
)


def _assert_contained(response) -> None:
    assert response.status_code == LEGACY_KNOWLEDGE_CONTAINED_STATUS
    detail = response.json()["detail"]
    assert detail["code"] == LEGACY_KNOWLEDGE_CONTAINED_CODE
    assert detail["message"] == LEGACY_KNOWLEDGE_CONTAINED_MESSAGE


@pytest.fixture
def kb_env(tmp_path, monkeypatch):
    db_path = tmp_path / "knowledge.db"
    projects_dir = tmp_path / "projects"
    monkeypatch.setenv("BEN_KNOWLEDGE_DB_PATH", str(db_path))
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(projects_dir))
    monkeypatch.setenv("BEN_LOCAL_BETA_MODE", "true")
    monkeypatch.setenv("BEN_BETA_PASSCODE", "beta-test-pass")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    init_knowledge_store()
    return {"db_path": db_path, "projects_dir": projects_dir, "slug": "shared-slug"}


def _beta_headers(alias: str = "auditor") -> dict[str, str]:
    return {
        "X-Basalt-Beta-Passcode": "beta-test-pass",
        "X-Basalt-Beta-Alias": alias,
    }


def _count_bases() -> int:
    with get_connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM knowledge_bases").fetchone()[0])


def _count_docs() -> int:
    with get_connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM knowledge_documents").fetchone()[0])


@pytest.mark.asyncio
async def test_few_shot_prompt_path_does_not_inject_existing_documents(kb_env):
    base = await create_knowledge_base("RMS")
    await add_knowledge_document(
        base["id"],
        title="Gold RMS Template",
        content="SECRET_SUPPLIER_DIRECTORY",
    )
    assert await list_knowledge_bases()
    assert await list_knowledge_documents(base["id"])

    inner = "<user_message>\nbuild an RMS based on the RMS base\n</user_message>"
    out = await inject_knowledge_few_shot("build an RMS based on the RMS base", inner)
    block = await build_knowledge_few_shot_block("build an RMS based on the RMS base")

    assert block == ""
    assert contained_few_shot_block("build an RMS based on the RMS base") == ""
    assert "<few_shot_examples>" not in out
    assert "SECRET_SUPPLIER_DIRECTORY" not in out
    assert "Gold RMS Template" not in out
    assert inner.strip() in out
    from services import chat_service as chat_mod

    assert chat_mod.inject_knowledge_few_shot is inject_knowledge_few_shot
    assert _count_docs() == 1
    assert kb_env["db_path"].exists()


@pytest.mark.asyncio
async def test_project_setup_prompt_path_does_not_inject_slug_context(kb_env):
    slug = kb_env["slug"]
    insert_context_record(
        slug,
        head=HEAD_CODE,
        title="private_contract_clause",
        content="TENANT_B_ONLY_PORTABLE_CONTEXT",
    )
    live = build_multi_head_prompt_context(slug, "private_contract_clause")
    assert "TENANT_B_ONLY_PORTABLE_CONTEXT" in live

    injected = contained_portable_project_context(slug, "private_contract_clause")
    assert injected == ""

    from services.project_agent_service import stream_project_agent_response

    thread_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    captured: dict[str, object] = {}

    async def fake_completion(*, messages, tools, tenant_id):
        captured["messages"] = messages
        return {
            "choices": [{"message": {"content": "Workspace is ready.", "tool_calls": []}}]
        }

    with (
        patch(
            "services.project_agent_service.get_thread_metadata",
            return_value={"project_slug": slug, "session_type": "project_setup"},
        ),
        patch(
            "services.project_tool_router.get_thread_session_type",
            lambda _tid: "project_setup",
        ),
        patch(
            "services.knowledge_store.build_multi_head_prompt_context",
            side_effect=AssertionError("slug knowledge must not enter project-setup prompts"),
        ),
        patch(
            "services.project_agent_service._openai_chat_completion",
            new=AsyncMock(side_effect=fake_completion),
        ),
        patch("services.project_agent_service.append_event", new=AsyncMock(return_value=None)),
    ):
        events = []
        async for line in stream_project_agent_response(
            user_message="Continue setup",
            tenant_id="00000000-0000-0000-0000-000000000001",
            thread_id=__import__("uuid").UUID(thread_id),
        ):
            events.append(line)

    assert events
    blob = str(captured.get("messages") or "")
    assert "TENANT_B_ONLY_PORTABLE_CONTEXT" not in blob
    assert "<portable_project_context>" not in blob


def test_global_knowledge_http_is_contained_and_does_not_mutate(kb_env):
    import main

    client = TestClient(main.app)
    before_bases = _count_bases()
    before_docs = _count_docs()

    _assert_contained(client.get("/api/knowledge/bases"))
    _assert_contained(client.get("/api/knowledge/bases", headers=_beta_headers("tenant-a")))
    _assert_contained(
        client.post("/api/knowledge/bases", json={"name": "ShouldNotCreate"})
    )
    _assert_contained(
        client.post(
            "/api/knowledge/bases",
            json={"name": "ShouldNotCreate"},
            headers=_beta_headers("tenant-b"),
        )
    )
    _assert_contained(client.delete("/api/knowledge/bases/1"))
    _assert_contained(client.get("/api/knowledge/bases/1/documents"))
    _assert_contained(
        client.post("/api/knowledge/bases/1/documents", json={"title": "x", "content": "secret"})
    )
    _assert_contained(client.delete("/api/knowledge/documents/1"))

    assert _count_bases() == before_bases
    assert _count_docs() == before_docs


@pytest.mark.asyncio
async def test_existing_sqlite_rows_remain_but_http_cannot_read_them(kb_env):
    base = await create_knowledge_base("PrivateDir")
    await add_knowledge_document(base["id"], title="Vendors", content="private-row")
    assert _count_docs() == 1

    import main

    client = TestClient(main.app)
    _assert_contained(client.get("/api/knowledge/bases"))
    _assert_contained(client.get(f"/api/knowledge/bases/{base['id']}/documents"))

    remaining = await list_knowledge_documents(base["id"])
    assert remaining[0]["content"] == "private-row"
    assert kb_env["db_path"].exists()


def test_project_slug_http_is_contained_and_does_not_write(kb_env):
    import main

    slug = kb_env["slug"]
    client = TestClient(main.app)
    knowledge_dir = kb_env["projects_dir"] / slug / "knowledge"

    _assert_contained(client.get(f"/api/projects/{slug}/knowledge/files"))
    _assert_contained(
        client.get(f"/api/projects/{slug}/knowledge/files", headers=_beta_headers("tenant-a"))
    )
    response = client.post(
        f"/api/projects/{slug}/knowledge/upload-stream",
        files={"file": ("secret.bin", b"cross-tenant", "application/octet-stream")},
        headers=_beta_headers("tenant-b"),
    )
    _assert_contained(response)
    _assert_contained(
        client.get(
            f"/api/projects/{slug}/threads/thread-alpha/active-attention",
            params={"query": "secret"},
            headers=_beta_headers("tenant-a"),
        )
    )

    assert list_knowledge_files(slug) == []
    assert not knowledge_dir.exists() or not any(knowledge_dir.iterdir())


@pytest.mark.asyncio
async def test_containment_does_not_delete_preexisting_project_files(kb_env):
    slug = kb_env["slug"]
    upload = UploadFile(filename="kept.log", file=io.BytesIO(b"keep-me"))
    record = await stream_knowledge_upload(slug, upload)
    stored = resolve_project_knowledge_dir(slug) / "kept.log"
    assert stored.exists()
    assert record["filename"] == "kept.log"

    import main

    client = TestClient(main.app)
    _assert_contained(
        client.get(f"/api/projects/{slug}/knowledge/files", headers=_beta_headers())
    )
    assert stored.exists()
    assert stored.read_bytes() == b"keep-me"
    assert list_knowledge_files(slug)[0]["id"] == record["id"]


def test_same_slug_http_cannot_share_attention_across_callers(kb_env):
    import main

    slug = kb_env["slug"]
    insert_context_record(
        slug,
        head=HEAD_CODE,
        title="shared-slug-secret",
        content="should-not-leave-via-http",
    )
    client = TestClient(main.app)
    for alias in ("org-a", "org-b"):
        _assert_contained(
            client.get(
                f"/api/projects/{slug}/threads/t1/active-attention",
                params={"query": "shared-slug-secret"},
                headers=_beta_headers(alias),
            )
        )
    live = build_multi_head_prompt_context(slug, "shared-slug-secret")
    assert "should-not-leave-via-http" in live
    assert contained_portable_project_context(slug, "shared-slug-secret") == ""
