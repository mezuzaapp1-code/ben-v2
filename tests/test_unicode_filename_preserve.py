"""Workspace Files: preserve Unicode display names; sanitize storage paths only."""
from __future__ import annotations

import hashlib
import io
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from services.message_format import decode_message, encode_chat_assistant
from services.workspace_files import service as file_service
from services.workspace_files import storage
from services.workspace_files.file_resolver import file_is_explicitly_named
from services.workspace_files.initial_read_pack import render_pack_evidence

HEBREW_PDF = "הצעה.pdf"
ASCII_PDF = "proposal.pdf"


class _Upload:
    def __init__(self, filename: str, content_type: str, data: bytes):
        self.filename = filename
        self.content_type = content_type
        self._b = io.BytesIO(data)

    async def read(self, n: int = -1) -> bytes:
        return self._b.read(n)


def _enable_durable(monkeypatch, root: Path) -> None:
    monkeypatch.setenv("BEN_REQUIRE_DURABLE_FILE_ROOT", "1")
    monkeypatch.setenv("BEN_PROJECTS_DATA_DIR", str(root))
    monkeypatch.delenv("BEN_DURABLE_FILE_MOUNT", raising=False)
    monkeypatch.delenv("RAILWAY_VOLUME_MOUNT_PATH", raising=False)


@pytest.fixture
def durable_root(tmp_path, monkeypatch):
    root = tmp_path / "projects"
    root.mkdir()
    _enable_durable(monkeypatch, root)
    return root


def test_preserve_hebrew_and_strip_path_and_controls() -> None:
    assert storage.preserve_original_filename(HEBREW_PDF) == HEBREW_PDF
    assert storage.preserve_original_filename(f"docs/{HEBREW_PDF}") == HEBREW_PDF
    assert storage.preserve_original_filename(f"..\\uploads\\{HEBREW_PDF}") == HEBREW_PDF
    assert storage.preserve_original_filename(f"\x00{HEBREW_PDF}\x07") == HEBREW_PDF
    long = ("א" * 600) + ".pdf"
    preserved = storage.preserve_original_filename(long)
    assert len(preserved) <= 512
    assert preserved.startswith("א")


def test_sanitize_filename_stays_storage_safe() -> None:
    safe = storage.sanitize_filename(HEBREW_PDF)
    assert all(ord(ch) < 128 for ch in safe)
    assert ".." not in safe
    assert "/" not in safe
    assert "\\" not in safe
    assert storage.sanitize_filename(ASCII_PDF) == ASCII_PDF


def test_validate_upload_uses_original_suffix_for_hebrew_pdf() -> None:
    display, storage_name, media, processable = file_service._validate_upload_name(HEBREW_PDF)
    assert display == HEBREW_PDF
    assert media == "application/pdf"
    assert processable is True
    assert Path(storage_name).suffix.lower() in {".pdf", ""} or storage_name.endswith("pdf")
    assert HEBREW_PDF not in storage_name
    display_ascii, storage_ascii, media_ascii, _ok = file_service._validate_upload_name(ASCII_PDF)
    assert display_ascii == ASCII_PDF
    assert storage_ascii == ASCII_PDF
    assert media_ascii == "application/pdf"


def test_validate_upload_rejects_hebrew_exe() -> None:
    with pytest.raises(file_service.HTTPException) as exc:
        file_service._validate_upload_name("הצעה.exe")
    assert exc.value.status_code == 400


def test_storage_key_hebrew_is_safe_and_traversal_cannot_escape(durable_root) -> None:
    org = uuid.UUID("11111111-1111-1111-1111-111111111111")
    ws = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
    fid = uuid.UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
    key = storage.storage_key_for(org, ws, fid, f"../{HEBREW_PDF}")
    assert ".." not in key
    assert HEBREW_PDF not in key
    assert str(org) in key and str(ws) in key and str(fid) in key
    path = storage.absolute_path_for_key(key)
    root = (durable_root / "_workspace_files").resolve()
    assert str(path).startswith(str(root))
    assert path == (root / key).resolve()

    with pytest.raises(ValueError, match="invalid storage key"):
        storage.absolute_path_for_key("../../etc/passwd")


def _capture_session(enqueued: dict):
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

    return _Ctx()


@pytest.mark.asyncio
async def test_upload_persists_hebrew_display_and_safe_storage(durable_root, monkeypatch):
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "on")
    monkeypatch.setattr(file_service, "_require_workspace", AsyncMock(return_value=object()))
    monkeypatch.setattr(file_service, "record_chat_upload", AsyncMock(return_value=None))
    monkeypatch.setattr(file_service, "schedule_upload_wake", lambda *_a, **_k: None)
    monkeypatch.setattr(file_service, "enqueue_document_processing_job", AsyncMock())
    monkeypatch.setattr(file_service, "attach_request_id", lambda payload: payload)
    enqueued: dict = {}
    monkeypatch.setattr(file_service, "get_db_session", lambda: _capture_session(enqueued))

    org = uuid.uuid4()
    ws = uuid.uuid4()
    body = b"%PDF-1.4 hebrew-name"
    payload = await file_service.upload_file(
        org_id=org,
        workspace_id=ws,
        upload=_Upload(f"folder/{HEBREW_PDF}", "application/pdf", body),
        uploaded_by="tester",
    )
    row = enqueued["row"]
    assert row.original_filename == HEBREW_PDF
    assert row.display_name == HEBREW_PDF
    assert payload["original_filename"] == HEBREW_PDF
    assert payload["display_name"] == HEBREW_PDF
    assert payload["media_type"] == "application/pdf"
    assert ".." not in row.storage_key
    assert HEBREW_PDF not in row.storage_key
    assert all(ord(ch) < 128 for ch in Path(row.storage_key).name)
    dest = storage.absolute_path_for_key(row.storage_key)
    assert dest.read_bytes() == body
    assert dest.stat().st_size == len(body)
    assert hashlib.sha256(body).hexdigest() == row.checksum
    root = (durable_root / "_workspace_files").resolve()
    assert str(dest.resolve()).startswith(str(root))


@pytest.mark.asyncio
async def test_upload_ascii_proposal_unchanged(durable_root, monkeypatch):
    monkeypatch.setenv("BEN_DOC_PROCESSING_ENABLED", "on")
    monkeypatch.setattr(file_service, "_require_workspace", AsyncMock(return_value=object()))
    monkeypatch.setattr(file_service, "record_chat_upload", AsyncMock(return_value=None))
    monkeypatch.setattr(file_service, "schedule_upload_wake", lambda *_a, **_k: None)
    monkeypatch.setattr(file_service, "enqueue_document_processing_job", AsyncMock())
    monkeypatch.setattr(file_service, "attach_request_id", lambda payload: payload)
    enqueued: dict = {}
    monkeypatch.setattr(file_service, "get_db_session", lambda: _capture_session(enqueued))

    payload = await file_service.upload_file(
        org_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        upload=_Upload(ASCII_PDF, "application/pdf", b"%PDF-1.4 ascii"),
        uploaded_by="tester",
    )
    row = enqueued["row"]
    assert row.original_filename == ASCII_PDF
    assert row.display_name == ASCII_PDF
    assert payload["display_name"] == ASCII_PDF
    assert row.storage_key.endswith(f"/{ASCII_PDF}")


def test_initial_read_pack_and_used_files_keep_hebrew() -> None:
    evidence = render_pack_evidence(
        display_name=HEBREW_PDF,
        file_id=uuid.UUID("a0000000-0000-0000-0000-000000000001"),
        page_count=3,
        extraction_status="complete",
        pages_extracted=3,
        pages_needs_ocr=0,
        chunks=[],
    )
    assert f'name="{HEBREW_PDF}"' in evidence

    used = file_service._used_files_payload(
        [(uuid.UUID("a0000000-0000-0000-0000-000000000001"), HEBREW_PDF)]
    )
    assert used == ({"id": "a0000000-0000-0000-0000-000000000001", "name": HEBREW_PDF},)

    encoded = encode_chat_assistant(
        "overview",
        used_files=[{"id": "a0000000-0000-0000-0000-000000000001", "name": HEBREW_PDF}],
        source_event="file_initial_read",
        source_file_id="a0000000-0000-0000-0000-000000000001",
    )
    decoded = decode_message("assistant", encoded)
    assert decoded["used_files"][0]["name"] == HEBREW_PDF
    assert HEBREW_PDF in encoded


def test_explicit_hebrew_filename_match() -> None:
    assert file_is_explicitly_named("summarize הצעה", HEBREW_PDF, HEBREW_PDF) is True
    assert file_is_explicitly_named("summarize the proposal", HEBREW_PDF, HEBREW_PDF) is False
    assert file_is_explicitly_named(f"open {ASCII_PDF}", ASCII_PDF, ASCII_PDF) is True
