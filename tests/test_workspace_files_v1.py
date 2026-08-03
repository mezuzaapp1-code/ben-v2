"""Workspace File Library V1 — auth, ownership, search bounds, chat path."""
from __future__ import annotations

import io
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@127.0.0.1:5432/test")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-openai")

import main  # noqa: E402
from auth.tenant_binding import TenantContext  # noqa: E402
from services.workspace_files import service as file_service  # noqa: E402
from services.workspace_files.types import MAX_UPLOAD_BYTES  # noqa: E402

ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
ORG_B = uuid.UUID("22222222-2222-2222-2222-222222222222")
WS_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
WS_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
FILE_A = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLERK_SECRET_KEY", "sk_test_clerk")
    monkeypatch.setenv("ENFORCE_AUTH", "false")
    monkeypatch.setenv("AUTH_SHADOW_MODE", "true")
    monkeypatch.setenv("BEN_ANONYMOUS_ORG_ID", "00000000-0000-0000-0000-000000000001")
    monkeypatch.delenv("BEN_LOCAL_BETA_MODE", raising=False)


def _ctx(org_id: uuid.UUID = ORG_A, user_id: str = "user_a") -> TenantContext:
    return TenantContext(
        tenant_id=str(org_id),
        tenant_type="organization",
        user_id=user_id,
        org_id=str(org_id),
        org_role="org:member",
        email="a@example.com",
        auth_source="clerk_jwt",
        auth_present=True,
        org_bound=True,
    )


def _file_payload(**overrides):
    base = {
        "id": str(FILE_A),
        "organization_id": str(ORG_A),
        "workspace_id": str(WS_A),
        "project_id": str(WS_A),
        "original_filename": "notes.txt",
        "display_name": "notes.txt",
        "media_type": "text/plain",
        "byte_size": 12,
        "checksum": "abc",
        "status": "ready",
        "uploaded_by": "user_a",
        "source_chat_id": None,
        "failure_code": None,
        "failure_message": None,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "has_extracted_text": True,
        "preview_kind": "text",
        "request_id": "req",
    }
    base.update(overrides)
    return base


def test_upload_requires_auth_when_enforced(monkeypatch):
    monkeypatch.setenv("ENFORCE_AUTH", "true")
    client = TestClient(main.app)
    res = client.post(
        f"/api/workspaces/{WS_A}/files",
        files={"file": ("notes.txt", b"hello world", "text/plain")},
    )
    assert res.status_code == 401


def test_authenticated_upload_succeeds_and_bound_to_workspace():
    payload = _file_payload(source_chat_id="chat-1")
    with patch(
        "routers.workspace_files._require_files_tenant",
        new_callable=AsyncMock,
        return_value=_ctx(),
    ), patch(
        "routers.workspace_files.file_service.upload_file",
        new_callable=AsyncMock,
        return_value=payload,
    ) as upload_mock:
        client = TestClient(main.app)
        res = client.post(
            f"/api/workspaces/{WS_A}/files",
            headers={"Authorization": "Bearer t"},
            files={"file": ("notes.txt", b"hello world", "text/plain")},
            data={"source_chat_id": "chat-1"},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["workspace_id"] == str(WS_A)
    assert body["organization_id"] == str(ORG_A)
    assert body["status"] == "ready"
    assert upload_mock.await_args.kwargs["workspace_id"] == WS_A
    assert upload_mock.await_args.kwargs["source_chat_id"] == "chat-1"


def test_missing_workspace_rejected():
    with patch(
        "routers.workspace_files._require_files_tenant",
        new_callable=AsyncMock,
        return_value=_ctx(),
    ), patch(
        "routers.workspace_files.file_service.upload_file",
        new_callable=AsyncMock,
        side_effect=file_service.HTTPException(404, "Workspace not found"),
    ):
        client = TestClient(main.app)
        res = client.post(
            f"/api/workspaces/{WS_B}/files",
            headers={"Authorization": "Bearer t"},
            files={"file": ("notes.txt", b"hello", "text/plain")},
        )
    assert res.status_code == 404


def test_unsupported_type_fails_clearly():
    with patch(
        "routers.workspace_files._require_files_tenant",
        new_callable=AsyncMock,
        return_value=_ctx(),
    ), patch(
        "routers.workspace_files.file_service.upload_file",
        new_callable=AsyncMock,
        side_effect=file_service.HTTPException(
            400, "File type not allowed for security reasons: .exe"
        ),
    ):
        client = TestClient(main.app)
        res = client.post(
            f"/api/workspaces/{WS_A}/files",
            headers={"Authorization": "Bearer t"},
            files={"file": ("malware.exe", b"MZ", "application/octet-stream")},
        )
    assert res.status_code == 400
    assert "not allowed" in res.json()["detail"].lower() or "unsupported" in res.json()[
        "detail"
    ].lower()


def test_list_and_search_are_workspace_bounded():
    with patch(
        "routers.workspace_files._require_files_tenant",
        new_callable=AsyncMock,
        return_value=_ctx(),
    ), patch(
        "routers.workspace_files.file_service.list_files",
        new_callable=AsyncMock,
        return_value={
            "items": [_file_payload()],
            "count": 1,
            "workspace_id": str(WS_A),
            "max_upload_bytes": MAX_UPLOAD_BYTES,
            "supported_extensions": [".txt"],
        },
    ) as list_mock:
        client = TestClient(main.app)
        res = client.get(
            f"/api/workspaces/{WS_A}/files?q=notes",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 200
    assert res.json()["workspace_id"] == str(WS_A)
    assert list_mock.await_args.kwargs["workspace_id"] == WS_A
    assert list_mock.await_args.kwargs["q"] == "notes"


def test_cross_workspace_get_rejected():
    with patch(
        "routers.workspace_files._require_files_tenant",
        new_callable=AsyncMock,
        return_value=_ctx(),
    ), patch(
        "routers.workspace_files.file_service.get_file",
        new_callable=AsyncMock,
        side_effect=file_service.HTTPException(404, "File not found"),
    ):
        client = TestClient(main.app)
        res = client.get(
            f"/api/workspaces/{WS_B}/files/{FILE_A}",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 404


def test_delete_authorization_enforced():
    with patch(
        "routers.workspace_files._require_files_tenant",
        new_callable=AsyncMock,
        return_value=_ctx(),
    ), patch(
        "routers.workspace_files.file_service.delete_file",
        new_callable=AsyncMock,
        side_effect=file_service.HTTPException(404, "File not found"),
    ):
        client = TestClient(main.app)
        res = client.delete(
            f"/api/workspaces/{WS_B}/files/{FILE_A}",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 404


def test_project_alias_chat_upload_path():
    payload = _file_payload(source_chat_id="thread-9")
    with patch(
        "routers.workspace_files._require_files_tenant",
        new_callable=AsyncMock,
        return_value=_ctx(),
    ), patch(
        "routers.workspace_files.file_service.upload_file",
        new_callable=AsyncMock,
        return_value=payload,
    ) as upload_mock:
        client = TestClient(main.app)
        res = client.post(
            f"/api/projects/{WS_A}/files",
            headers={"Authorization": "Bearer t"},
            files={"file": ("brief.md", b"# hi", "text/markdown")},
            data={"source_chat_id": "thread-9"},
        )
    assert res.status_code == 200
    assert res.json()["source_chat_id"] == "thread-9"
    assert upload_mock.await_args.kwargs["source_chat_id"] == "thread-9"


def test_validate_upload_name_rejects_exe_and_accepts_pdf():
    with pytest.raises(file_service.HTTPException) as exc:
        file_service._validate_upload_name("bad.exe")
    assert exc.value.status_code == 400

    safe, media, processable = file_service._validate_upload_name("report.PDF")
    assert safe.lower().endswith(".pdf")
    assert media == "application/pdf"
    assert processable is True


def test_validate_size_limit_constant():
    assert MAX_UPLOAD_BYTES == 50 * 1024 * 1024


@pytest.mark.asyncio
async def test_process_status_transitions_and_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(file_service.storage, "files_root", lambda: tmp_path)
    monkeypatch.setattr(
        file_service.storage,
        "absolute_path_for_key",
        lambda key: tmp_path / key,
    )

    org = ORG_A
    ws = WS_A
    fid = FILE_A
    key = f"{org}/{ws}/{fid}/notes.txt"
    dest = tmp_path / key
    dest.parent.mkdir(parents=True)
    dest.write_text("alpha beta searchable", encoding="utf-8")

    class Row:
        def __init__(self):
            self.id = fid
            self.org_id = org
            self.workspace_id = ws
            self.project_id = ws
            self.original_filename = "notes.txt"
            self.display_name = "notes.txt"
            self.media_type = "text/plain"
            self.byte_size = 20
            self.checksum = "x"
            self.storage_key = key
            self.status = "uploaded"
            self.uploaded_by = "user_a"
            self.source_chat_id = "chat"
            self.extracted_text = None
            self.failure_code = None
            self.failure_message = None
            self.created_at = datetime.now(timezone.utc)
            self.updated_at = datetime.now(timezone.utc)

    row = Row()
    statuses = []

    class FakeSession:
        async def execute(self, *_a, **_k):
            return None

        async def get(self, _model, _id):
            return row

        def add(self, _obj):
            return None

        async def commit(self):
            statuses.append(row.status)

        async def refresh(self, _obj):
            return None

        async def delete(self, _obj):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    class FakeResult:
        def scalars(self):
            return self

        def all(self):
            return [row]

    class ListSession(FakeSession):
        async def execute(self, *_a, **_k):
            return FakeResult()

    project = MagicMock()
    project.org_id = org

    with patch.object(file_service, "_require_workspace", new_callable=AsyncMock, return_value=project):
        with patch.object(file_service, "get_db_session", side_effect=lambda: FakeSession()):
            out = await file_service.process_file(org_id=org, workspace_id=ws, file_id=fid)
    assert out["status"] == "ready"
    assert "alpha beta" in (row.extracted_text or "")
    assert "queued" in statuses and "processing" in statuses and "ready" in statuses

    # Missing bytes → failed
    row.status = "uploaded"
    row.extracted_text = None
    dest.unlink()
    with patch.object(file_service, "_require_workspace", new_callable=AsyncMock, return_value=project):
        with patch.object(file_service, "get_db_session", side_effect=lambda: FakeSession()):
            failed = await file_service.process_file(org_id=org, workspace_id=ws, file_id=fid)
    assert failed["status"] == "failed"
    assert failed["failure_code"] == "missing_bytes"


@pytest.mark.asyncio
async def test_list_query_requires_workspace_match(monkeypatch):
    project = MagicMock()
    project.org_id = ORG_A

    class EmptyResult:
        def scalars(self):
            return self

        def all(self):
            return []

    class Sess:
        async def execute(self, *_a, **_k):
            return EmptyResult()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_e):
            return False

    with patch.object(file_service, "_require_workspace", new_callable=AsyncMock, return_value=project) as req:
        with patch.object(file_service, "get_db_session", side_effect=lambda: Sess()):
            out = await file_service.list_files(
                org_id=ORG_A, workspace_id=WS_A, q="secret", limit=10
            )
    assert out["workspace_id"] == str(WS_A)
    assert req.await_args.args[1] == WS_A or req.await_args.kwargs.get("workspace_id") == WS_A


def test_content_download_uses_authz_path(tmp_path):
    path = tmp_path / "f.txt"
    path.write_text("x", encoding="utf-8")
    with patch(
        "routers.workspace_files._require_files_tenant",
        new_callable=AsyncMock,
        return_value=_ctx(),
    ), patch(
        "routers.workspace_files.file_service.open_file_bytes",
        new_callable=AsyncMock,
        return_value=(path, "text/plain", "f.txt"),
    ):
        client = TestClient(main.app)
        res = client.get(
            f"/api/workspaces/{WS_A}/files/{FILE_A}/content",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 200
    assert res.content == b"x"


def test_chat_upload_appears_in_list_contract():
    """Chat-uploaded file uses same list endpoint / workspace id."""
    listed = {
        "items": [_file_payload(source_chat_id="chat-42", display_name="from-chat.txt")],
        "count": 1,
        "workspace_id": str(WS_A),
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "supported_extensions": [".txt"],
    }
    with patch(
        "routers.workspace_files._require_files_tenant",
        new_callable=AsyncMock,
        return_value=_ctx(),
    ), patch(
        "routers.workspace_files.file_service.list_files",
        new_callable=AsyncMock,
        return_value=listed,
    ):
        client = TestClient(main.app)
        res = client.get(
            f"/api/workspaces/{WS_A}/files",
            headers={"Authorization": "Bearer t"},
        )
    assert res.status_code == 200
    item = res.json()["items"][0]
    assert item["source_chat_id"] == "chat-42"
    assert item["workspace_id"] == str(WS_A)
