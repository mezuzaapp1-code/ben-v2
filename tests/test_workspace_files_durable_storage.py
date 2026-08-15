"""Gate 3E — durable WorkspaceFile byte persistence fail-closed safety."""
from __future__ import annotations

import hashlib
import io
import os
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from services.workspace_files import service as file_service
from services.workspace_files import storage


class _Upload:
    def __init__(self, filename: str, content_type: str, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._b = io.BytesIO(data)

    async def read(self, n: int = -1) -> bytes:
        return self._b.read(n)


def _enable_durable(monkeypatch, root: Path, *, mount: Path | None = None) -> None:
    monkeypatch.setenv("BEN_REQUIRE_DURABLE_FILE_ROOT", "1")
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(root))
    if mount is not None:
        monkeypatch.setenv("BEN_DURABLE_FILE_MOUNT", str(mount))
    else:
        monkeypatch.delenv("BEN_DURABLE_FILE_MOUNT", raising=False)
        monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)


@pytest.fixture
def durable_root(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    _enable_durable(monkeypatch, root)
    return root


# ---------------------------------------------------------------------------
# A. durable root configured + valid → upload succeeds
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_durable_root_configured_upload_succeeds(durable_root):
    org = uuid.uuid4()
    ws = uuid.uuid4()
    fid = uuid.uuid4()
    body = b"gate3e durable bytes"
    key, size, checksum = await storage.write_upload(
        org_id=org,
        workspace_id=ws,
        file_id=fid,
        filename="canary.txt",
        upload=_Upload("canary.txt", "text/plain", body),
    )
    assert key == f"{org}/{ws}/{fid}/canary.txt"
    assert size == len(body)
    assert checksum == hashlib.sha256(body).hexdigest()
    dest = storage.absolute_path_for_key(key)
    assert dest.exists()
    assert dest.read_bytes() == body
    assert dest.stat().st_size == len(body)
    assert str(dest).startswith(str((durable_root / "_workspace_files").resolve()))


@pytest.mark.asyncio
async def test_a_durable_root_under_existing_mount_creates_projects_dir(tmp_path, monkeypatch):
    mount = tmp_path / "data"
    mount.mkdir()
    root = mount / "projects"
    _enable_durable(monkeypatch, root, mount=mount)
    assert not root.exists()
    key, size, checksum = await storage.write_upload(
        org_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        file_id=uuid.uuid4(),
        filename="mounted.txt",
        upload=_Upload("mounted.txt", "text/plain", b"on-volume"),
    )
    assert size == 9
    assert checksum == hashlib.sha256(b"on-volume").hexdigest()
    assert storage.absolute_path_for_key(key).read_bytes() == b"on-volume"
    assert root.is_dir()


# ---------------------------------------------------------------------------
# B. durable root required but unavailable → upload fails closed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_b_required_but_data_dir_missing_fails_closed(tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist"
    _enable_durable(monkeypatch, missing)
    with pytest.raises(storage.DurableStorageUnavailable, match="existing directory"):
        await storage.write_upload(
            org_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            file_id=uuid.uuid4(),
            filename="x.txt",
            upload=_Upload("x.txt", "text/plain", b"nope"),
        )
    assert not missing.exists()


@pytest.mark.asyncio
async def test_b_required_but_data_dir_unset_fails_closed(monkeypatch, tmp_path):
    monkeypatch.setenv("BEN_REQUIRE_DURABLE_FILE_ROOT", "1")
    monkeypatch.delenv("BEN_PROJECTS_DATA_DIR", raising=False)
    monkeypatch.delenv("BEN_DURABLE_FILE_MOUNT", raising=False)
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)
    with pytest.raises(storage.DurableStorageUnavailable, match="BEN_PROJECTS_DATA_DIR is required"):
        storage.assert_durable_root_ready()
    with pytest.raises(storage.DurableStorageUnavailable):
        await storage.write_upload(
            org_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            file_id=uuid.uuid4(),
            filename="x.txt",
            upload=_Upload("x.txt", "text/plain", b"nope"),
        )


@pytest.mark.asyncio
async def test_b_required_but_mount_missing_fails_closed(tmp_path, monkeypatch):
    mount = tmp_path / "not-mounted"
    root = mount / "projects"
    _enable_durable(monkeypatch, root, mount=mount)
    with pytest.raises(storage.DurableStorageUnavailable, match="not an existing directory"):
        await storage.write_upload(
            org_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            file_id=uuid.uuid4(),
            filename="x.txt",
            upload=_Upload("x.txt", "text/plain", b"nope"),
        )
    assert not root.exists()


@pytest.mark.asyncio
async def test_b_required_but_root_outside_mount_fails_closed(tmp_path, monkeypatch):
    mount = tmp_path / "data"
    mount.mkdir()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    _enable_durable(monkeypatch, outside, mount=mount)
    with pytest.raises(storage.DurableStorageUnavailable, match="not under the durable mount"):
        await storage.write_upload(
            org_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            file_id=uuid.uuid4(),
            filename="x.txt",
            upload=_Upload("x.txt", "text/plain", b"nope"),
        )


# ---------------------------------------------------------------------------
# C/D. failed durable persistence → no WorkspaceFile row, no processing job
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cd_failed_durable_persistence_does_not_touch_db(monkeypatch, tmp_path):
    missing = tmp_path / "missing-durable"
    _enable_durable(monkeypatch, missing)
    monkeypatch.setattr(file_service, "_require_workspace", AsyncMock(return_value=object()))

    def db_must_not_open(*_a, **_k):
        raise AssertionError("get_db_session must not run when durable persist fails")

    async def enqueue_must_not_run(*_a, **_k):
        raise AssertionError("enqueue_document_processing_job must not run when durable persist fails")

    monkeypatch.setattr(file_service, "get_db_session", db_must_not_open)
    monkeypatch.setattr(file_service, "enqueue_document_processing_job", enqueue_must_not_run)
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "on")

    with pytest.raises(HTTPException) as exc_info:
        await file_service.upload_file(
            org_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            upload=_Upload("notes.txt", "text/plain", b"should not persist"),
            uploaded_by="tester",
        )
    assert exc_info.value.status_code == 503
    assert not missing.exists()


@pytest.mark.asyncio
async def test_cd_verify_failure_unlinks_bytes_and_skips_db(durable_root, monkeypatch):
    monkeypatch.setattr(file_service, "_require_workspace", AsyncMock(return_value=object()))
    monkeypatch.setattr(
        file_service,
        "get_db_session",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("get_db_session must not run when verify fails")
        ),
    )
    monkeypatch.setattr(
        file_service,
        "enqueue_document_processing_job",
        AsyncMock(side_effect=AssertionError("job must not enqueue")),
    )
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "on")

    def boom(dest, *, expected_size, expected_checksum):
        raise storage.DurableStorageUnavailable("persisted file checksum mismatch after write")

    monkeypatch.setattr(storage, "_verify_persisted_file", boom)
    org = uuid.uuid4()
    ws = uuid.uuid4()
    with pytest.raises(HTTPException) as exc_info:
        await file_service.upload_file(
            org_id=org,
            workspace_id=ws,
            upload=_Upload("notes.txt", "text/plain", b"verify-fail"),
            uploaded_by="tester",
        )
    assert exc_info.value.status_code == 503
    leftover = list((durable_root / "_workspace_files").rglob("notes.txt"))
    assert leftover == []


# ---------------------------------------------------------------------------
# E. existing storage_key behavior unchanged
# ---------------------------------------------------------------------------
def test_e_storage_key_format_unchanged(durable_root):
    org = uuid.UUID("11111111-1111-1111-1111-111111111111")
    ws = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    fid = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    key = storage.storage_key_for(org, ws, fid, "PRICE QUOTATION.pdf")
    assert key == f"{org}/{ws}/{fid}/PRICE QUOTATION.pdf"
    path = storage.absolute_path_for_key(key)
    assert path == (storage.files_root() / key).resolve()
    assert path == (durable_root / "_workspace_files" / key).resolve()


def test_e_storage_key_rejects_escape(durable_root):
    with pytest.raises(ValueError, match="invalid storage key"):
        storage.absolute_path_for_key("../../etc/passwd")


# ---------------------------------------------------------------------------
# F. existing synchronous path unchanged
# ---------------------------------------------------------------------------
def test_f_synchronous_path_unchanged_when_flag_off():
    src = Path("services/workspace_files/service.py").read_text()
    upload_fn = src.split("async def upload_file", 1)[1].split("async def process_file", 1)[0]
    assert "_doc_processing_enabled" in upload_fn
    assert "await process_file(" in upload_fn
    assert "write_upload" in upload_fn
    assert upload_fn.index("write_upload") < upload_fn.index("WorkspaceFile(")
    assert upload_fn.index("write_upload") < upload_fn.index("enqueue_document_processing_job")


# ---------------------------------------------------------------------------
# G. async path unchanged except persistence prerequisite
# ---------------------------------------------------------------------------
def test_g_async_path_still_enqueues_after_persist():
    src = Path("services/workspace_files/service.py").read_text()
    upload_fn = src.split("async def upload_file", 1)[1].split("async def process_file", 1)[0]
    assert "enqueue_document_processing_job" in upload_fn
    assert "DurableStorageUnavailable" in upload_fn
    assert "HTTP_503_SERVICE_UNAVAILABLE" in upload_fn
    assert upload_fn.index("DurableStorageUnavailable") < upload_fn.index("WorkspaceFile(")
    assert upload_fn.index("write_upload") < upload_fn.index("enqueue_document_processing_job")


@pytest.mark.asyncio
async def test_g_async_path_reaches_enqueue_after_durable_write(durable_root, monkeypatch):
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "on")
    monkeypatch.setattr(file_service, "_require_workspace", AsyncMock(return_value=object()))

    enqueued = {}

    class _Session:
        async def execute(self, *_a, **_k):
            return None

        def add(self, row):
            enqueued["row"] = row

        async def flush(self):
            return None

        async def commit(self):
            return None

        async def refresh(self, row):
            return None

    class _Ctx:
        async def __aenter__(self):
            return _Session()

        async def __aexit__(self, *_a):
            return False

    monkeypatch.setattr(file_service, "get_db_session", lambda: _Ctx())

    async def capture_enqueue(*_a, **_k):
        enqueued["job"] = True

    monkeypatch.setattr(file_service, "enqueue_document_processing_job", capture_enqueue)
    monkeypatch.setattr(file_service, "_payload", lambda row: {"id": str(row.id), "status": row.status})
    monkeypatch.setattr(file_service, "attach_request_id", lambda payload: payload)

    payload = await file_service.upload_file(
        org_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        upload=_Upload("async.txt", "text/plain", b"queued-after-persist"),
        uploaded_by="tester",
    )
    assert payload["status"] == "queued"
    assert enqueued.get("job") is True
    assert enqueued["row"].checksum == hashlib.sha256(b"queued-after-persist").hexdigest()


@pytest.mark.asyncio
async def test_require_off_preserves_ephemeral_write(tmp_path, monkeypatch):
    monkeypatch.delenv("BEN_REQUIRE_DURABLE_FILE_ROOT", raising=False)
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(tmp_path / "projects"))
    key, size, checksum = await storage.write_upload(
        org_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        file_id=uuid.uuid4(),
        filename="legacy.txt",
        upload=_Upload("legacy.txt", "text/plain", b"legacy"),
    )
    assert size == 6
    assert checksum == hashlib.sha256(b"legacy").hexdigest()
    assert storage.absolute_path_for_key(key).read_bytes() == b"legacy"


def test_verify_persisted_file_rejects_size_and_checksum(tmp_path):
    dest = tmp_path / "f.txt"
    dest.write_bytes(b"abc")
    with pytest.raises(storage.DurableStorageUnavailable, match="size mismatch"):
        storage._verify_persisted_file(dest, expected_size=2, expected_checksum="x")
    with pytest.raises(storage.DurableStorageUnavailable, match="checksum mismatch"):
        storage._verify_persisted_file(
            dest, expected_size=3, expected_checksum="0" * 64
        )
    storage._verify_persisted_file(
        dest, expected_size=3, expected_checksum=hashlib.sha256(b"abc").hexdigest()
    )
