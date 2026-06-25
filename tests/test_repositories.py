"""Project data repository layer — connect, upload, toggle."""
from __future__ import annotations

import io
import sqlite3

import pytest
from fastapi import UploadFile
from fastapi.testclient import TestClient

import main
from auth.beta_gate import derive_beta_org_id
from services.repository_store import (
    connect_repository,
    get_repository,
    init_project_repositories,
    list_repositories,
    resolve_repository_storage_dir,
    stream_repository_upload,
    toggle_repository,
)


@pytest.fixture
def project_env(tmp_path, monkeypatch):
    projects_dir = tmp_path / "projects"
    system_db = tmp_path / "system_main.db"
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(projects_dir))
    monkeypatch.setenv("BEN_SYSTEM_DB_PATH", str(system_db))
    monkeypatch.setenv("BEN_LOCAL_BETA_MODE", "true")
    monkeypatch.setenv("BEN_BETA_PASSCODE", "beta-test-pass")
    alias = "repo-user"
    return {
        "slug": "repo-demo",
        "org_id": str(derive_beta_org_id(alias)),
        "alias": alias,
    }


def _beta_headers(alias: str = "repo-user") -> dict[str, str]:
    return {
        "X-Basalt-Beta-Passcode": "beta-test-pass",
        "X-Basalt-Beta-Alias": alias,
    }


def test_init_project_repositories_creates_file_table(project_env):
    init_project_repositories(project_env["slug"])
    from services.knowledge_store import resolve_project_db_path

    with sqlite3.connect(resolve_project_db_path(project_env["slug"])) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='repository_files'"
        ).fetchone()
        assert row is not None


def test_connect_repository_google_drive_metadata_only(project_env):
    repo = connect_repository(
        project_env["org_id"],
        project_env["slug"],
        name="Drive Specs",
        source_type="google_drive",
        source_metadata={
            "catalog_key": "repo-gdrive",
            "folder_id": "abc123",
            "folder_name": "Field Manuals",
            "access_token": "secret-token",
        },
    )
    assert repo["source_type"] == "google_drive"
    assert repo["status"] == "active"
    assert repo["source_metadata"]["folder_id"] == "abc123"
    assert "access_token" not in repo["source_metadata"]


def test_toggle_repository_scrubs_tokens(project_env):
    repo = connect_repository(
        project_env["org_id"],
        project_env["slug"],
        name="External Library",
        source_type="external_library",
        source_metadata={
            "catalog_key": "repo-library",
            "library_id": "lib-9",
            "access_token": "rotate-me",
            "catalog_url": "https://example.com/catalog",
        },
    )
    toggled = toggle_repository(project_env["org_id"], repo["id"])
    assert toggled["status"] == "disconnected"
    assert "access_token" not in toggled["source_metadata"]
    assert toggled["source_metadata"]["catalog_url"] == "https://example.com/catalog"


@pytest.mark.asyncio
async def test_stream_repository_upload_writes_file(project_env):
    repo = connect_repository(
        project_env["org_id"],
        project_env["slug"],
        name="Local PDFs",
        source_type="local",
        source_metadata={"catalog_key": "repo-local", "root_hint": "manuals"},
    )
    payload = b"%PDF-" + (b"x" * 2048)
    upload = UploadFile(filename="manual.pdf", file=io.BytesIO(payload))

    record = await stream_repository_upload(
        project_env["org_id"],
        project_env["slug"],
        repo["id"],
        upload,
    )
    assert record["filename"] == "manual.pdf"
    assert record["size_bytes"] == len(payload)

    stored = resolve_repository_storage_dir(project_env["slug"], repo["id"]) / "manual.pdf"
    assert stored.exists()
    assert stored.read_bytes() == payload


@pytest.mark.asyncio
async def test_stream_repository_upload_rejects_disconnected_repo(project_env):
    repo = connect_repository(
        project_env["org_id"],
        project_env["slug"],
        name="Disabled",
        source_type="local",
        source_metadata={"catalog_key": "repo-local-off"},
    )
    toggle_repository(project_env["org_id"], repo["id"])
    upload = UploadFile(filename="blocked.pdf", file=io.BytesIO(b"data"))
    with pytest.raises(ValueError, match="disconnected"):
        await stream_repository_upload(
            project_env["org_id"],
            project_env["slug"],
            repo["id"],
            upload,
        )


def test_connect_repository_api(project_env):
    client = TestClient(main.app)
    response = client.post(
        f"/api/projects/{project_env['slug']}/repositories/connect",
        json={
            "name": "Google Drive Docs",
            "source_type": "google_drive",
            "source_metadata": {
                "catalog_key": "repo-gdrive",
                "folder_id": "gd-001",
                "folder_name": "Specs",
            },
        },
        headers=_beta_headers(project_env["alias"]),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["project_slug"] == project_env["slug"]
    assert body["repository"]["source_type"] == "google_drive"


def test_upload_repository_api(project_env):
    client = TestClient(main.app)
    connect = client.post(
        f"/api/projects/{project_env['slug']}/repositories/connect",
        json={
            "name": "Local",
            "source_type": "local",
            "source_metadata": {"catalog_key": "repo-local"},
        },
        headers=_beta_headers(project_env["alias"]),
    )
    repo_id = connect.json()["repository"]["id"]
    content = b"digital-book-content"
    response = client.post(
        f"/api/projects/{project_env['slug']}/repositories/upload",
        data={"repository_id": str(repo_id)},
        files={"file": ("book.pdf", content, "application/pdf")},
        headers=_beta_headers(project_env["alias"]),
    )
    assert response.status_code == 200
    assert response.json()["file"]["filename"] == "book.pdf"


def test_toggle_repository_api(project_env):
    client = TestClient(main.app)
    connect = client.post(
        f"/api/projects/{project_env['slug']}/repositories/connect",
        json={
            "name": "Drive",
            "source_type": "google_drive",
            "source_metadata": {
                "catalog_key": "repo-gdrive",
                "folder_id": "x",
                "access_token": "tok",
            },
        },
        headers=_beta_headers(project_env["alias"]),
    )
    repo_id = connect.json()["repository"]["id"]
    response = client.post(
        f"/api/projects/{project_env['slug']}/repositories/{repo_id}/toggle",
        headers=_beta_headers(project_env["alias"]),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["repository"]["status"] == "disconnected"
    assert body["tokens_scrubbed"] is True
    assert get_repository(project_env["org_id"], repo_id)["status"] == "disconnected"


def test_list_repositories_api(project_env):
    client = TestClient(main.app)
    connect = client.post(
        f"/api/projects/{project_env['slug']}/repositories/connect",
        json={
            "name": "Local",
            "source_type": "local",
            "source_metadata": {"catalog_key": "repo-local"},
        },
        headers=_beta_headers(project_env["alias"]),
    )
    assert connect.status_code == 200
    response = client.get(
        f"/api/projects/{project_env['slug']}/repositories",
        headers=_beta_headers(project_env["alias"]),
    )
    assert response.status_code == 200
    repos = response.json()["repositories"]
    assert len(repos) == 1
    assert repos[0]["source_metadata"]["catalog_key"] == "repo-local"


def test_list_repositories_after_connect(project_env):
    connect_repository(
        project_env["org_id"],
        project_env["slug"],
        name="A",
        source_type="local",
        source_metadata={"catalog_key": "repo-local-a"},
    )
    connect_repository(
        project_env["org_id"],
        project_env["slug"],
        name="B",
        source_type="external_library",
        source_metadata={"catalog_key": "repo-library-b"},
    )
    repos = list_repositories(project_env["org_id"])
    assert len(repos) == 2
