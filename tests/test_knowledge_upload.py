"""Resilient project knowledge upload streaming."""
from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

import main
from services.knowledge_store import (
    list_knowledge_files,
    resolve_project_knowledge_dir,
    stream_knowledge_upload,
)


@pytest.fixture
def project_env(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(projects_dir))
    monkeypatch.setenv("BEN_LOCAL_BETA_MODE", "true")
    monkeypatch.setenv("BEN_BETA_PASSCODE", "beta-test-pass")
    return "upload-demo"


def _beta_headers(alias: str = "uploader") -> dict[str, str]:
    return {
        "X-Basalt-Beta-Passcode": "beta-test-pass",
        "X-Basalt-Beta-Alias": alias,
    }


@pytest.mark.asyncio
async def test_stream_knowledge_upload_writes_file_and_metadata(project_env):
    slug = project_env
    payload = b"alpha" + (b"x" * (1024 * 1024)) + b"omega"
    upload = UploadFile(filename="dataset.log", file=io.BytesIO(payload))

    record = await stream_knowledge_upload(slug, upload)

    assert record["filename"] == "dataset.log"
    assert record["size_bytes"] == len(payload)
    assert record["status"] == "ready"

    stored_path = resolve_project_knowledge_dir(slug) / "dataset.log"
    assert stored_path.exists()
    assert stored_path.read_bytes() == payload

    files = list_knowledge_files(slug)
    assert len(files) == 1
    assert files[0]["id"] == record["id"]


@pytest.mark.asyncio
async def test_stream_knowledge_upload_cleans_partial_file_on_failure(project_env):
    slug = project_env

    class FailingReader:
        filename = "partial.bin"
        content_type = "application/octet-stream"

        def __init__(self) -> None:
            self._calls = 0

        async def read(self, size: int = -1) -> bytes:
            self._calls += 1
            if self._calls == 1:
                return b"x" * 1024
            raise ConnectionError("client disconnected")

    with pytest.raises(ConnectionError):
        await stream_knowledge_upload(slug, FailingReader())

    partial_path = resolve_project_knowledge_dir(slug) / "partial.bin"
    assert not partial_path.exists()
    assert list_knowledge_files(slug) == []


@pytest.mark.asyncio
async def test_stream_knowledge_upload_rejects_empty(project_env):
    upload = UploadFile(filename="empty.bin", file=io.BytesIO(b""))
    with pytest.raises(ValueError, match="empty upload"):
        await stream_knowledge_upload(project_env, upload)


def test_upload_stream_api_endpoint_is_contained(project_env):
    client = TestClient(main.app)
    content = b"log-line\n" * 128
    response = client.post(
        f"/api/projects/{project_env}/knowledge/upload-stream",
        files={"file": ("server.log", content, "text/plain")},
        headers=_beta_headers(),
    )
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "legacy_knowledge_contained"
    assert list_knowledge_files(project_env) == []


def test_list_knowledge_files_api_endpoint_is_contained(project_env):
    client = TestClient(main.app)
    response = client.get(
        f"/api/projects/{project_env}/knowledge/files",
        headers=_beta_headers(),
    )
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "legacy_knowledge_contained"


def test_active_attention_api_endpoint_is_contained(project_env):
    from services.knowledge_store import HEAD_CODE, insert_context_record

    client = TestClient(main.app)
    slug = project_env
    insert_context_record(
        slug,
        head=HEAD_CODE,
        title="query_hybrid_attention",
        content="def query_hybrid_attention(project_slug, query_text): ...",
    )
    response = client.get(
        f"/api/projects/{slug}/threads/thread-alpha/active-attention",
        params={"query": "query_hybrid_attention"},
        headers=_beta_headers(),
    )
    assert response.status_code == 410
    assert response.json()["detail"]["code"] == "legacy_knowledge_contained"
