"""Image-only source lifecycle: valid stored image != file failure.

No OCR, Vision, 4A enablement, or provider-adapter changes.
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import types
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://ben:ben@127.0.0.1:5432/ben")

import services.chat_service as chat_service
from services.workspace_files.drain import processing_completed_without_text
from services.workspace_files.document_parser import (
    ImagePlaceholderParser,
    _assemble_document,
    resolve_parser,
)
from services.workspace_files.extraction_pipeline import (
    _legacy_projection,
    valid_source_without_text,
)
from services.workspace_files.file_resolver import eligible_from_row
from services.workspace_files.service import (
    _payload,
    _preview_kind,
    load_ready_files_context,
    open_file_bytes,
)
from services.workspace_files.chunk_retriever import ready_file_from_row

ORG_A = uuid.UUID("11111111-1111-1111-1111-111111111111")
WS_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
IMAGE_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
TEXT_ID = uuid.UUID("44444444-4444-4444-8444-444444444444")


def _row(
    *,
    org_id=ORG_A,
    workspace_id=WS_A,
    status="ready",
    text="",
    name="scan.png",
        created_at=None,
    rid=None,
    media_type="image/png",
    index_status="not_indexed",
    indexed_chunk_count=0,
    extraction_status="failed",
    extraction_truncated=False,
    failure_code=None,
    failure_message=None,
    checksum="abc",
    byte_size=12,
    uploaded_by=None,
    source_chat_id=None,
    created_iso=None,
    updated_iso=None,
    storage_key="k",
):
    return types.SimpleNamespace(
        org_id=org_id,
        workspace_id=workspace_id,
        project_id=workspace_id,
        status=status,
        extracted_text=text,
        display_name=name,
        original_filename=name,
        created_at=created_at,
        updated_at=updated_iso,
        id=rid or uuid.uuid4(),
        media_type=media_type,
        byte_size=byte_size,
        checksum=checksum,
        uploaded_by=uploaded_by,
        source_chat_id=source_chat_id,
        failure_code=failure_code,
        failure_message=failure_message,
        storage_key=storage_key,
        index_status=index_status,
        indexed_chunk_count=indexed_chunk_count,
        extraction_status=extraction_status,
        extraction_truncated=extraction_truncated,
        indexed_at=None,
        job_status=None,
    )


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, *a, **k):
        return _FakeResult(self._rows)


def _patch_session(monkeypatch, rows):
    monkeypatch.setattr(
        "services.workspace_files.service.get_db_session",
        lambda: _FakeSession(rows),
    )


def test_placeholder_parser_png_and_jpg_are_needs_ocr():
    for name, media in (("scan.png", "image/png"), ("photo.jpg", "image/jpeg")):
        parser = resolve_parser(media, name)
        assert isinstance(parser, ImagePlaceholderParser)
        doc = parser.parse(Path(name), media_type=media, filename=name)
        assert valid_source_without_text(doc) is True
        assert [p.status for p in doc.pages] == ["needs_ocr"]
        status, text, code, message = _legacy_projection(doc, "failed")
        assert status == "ready"
        assert text == ""
        assert code is None
        assert message is None


def test_payload_image_ready_has_no_extracted_text():
    row = _row(rid=IMAGE_ID, text="", media_type="image/png", name="scan.png")
    payload = _payload(row, job_status="succeeded")
    assert payload["status"] == "ready"
    assert payload["has_extracted_text"] is False
    assert payload["preview_kind"] == "image"
    assert payload["failure_code"] is None
    assert payload["job_status"] == "succeeded"


def test_drain_treats_valid_no_text_as_success_not_source_failure():
    assert processing_completed_without_text(
        {"valid_source_without_text": True, "error": None, "final_extraction_status": "failed"}
    ) is True
    assert processing_completed_without_text(
        {"valid_source_without_text": False, "error": None, "final_extraction_status": "failed"}
    ) is False
    assert processing_completed_without_text(
        {"valid_source_without_text": True, "error": "missing_bytes"}
    ) is False


def test_preview_and_download_do_not_require_extracted_text():
    assert _preview_kind("image/png", "scan.png") == "image"
    assert _preview_kind("image/jpeg", "photo.jpg") == "image"
    src = inspect.getsource(open_file_bytes)
    assert "extracted_text" not in src
    assert 'status == "ready"' not in src
    assert "status != " not in src


@pytest.mark.asyncio
async def test_image_only_not_eligible_for_gate3d(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    image = _row(rid=IMAGE_ID, text="", name="scan.png")
    text = _row(rid=TEXT_ID, text="The verified opening width is 1370 mm.", name="F0-test.txt")
    assert eligible_from_row(image, ORG_A, WS_A) is None
    _patch_session(monkeypatch, [image, text])
    out = await load_ready_files_context(
        ORG_A, WS_A, max_chars=10_000, user_query="What is the verified opening width?"
    )
    assert out.retrieval_mode == "off"
    used_ids = {item["id"] for item in out.used_files}
    assert str(IMAGE_ID) not in used_ids
    assert str(TEXT_ID) in used_ids
    assert "scan.png" not in out.block
    assert "1370" in out.block


@pytest.mark.asyncio
async def test_image_only_not_eligible_for_gate4a_text_retrieval(monkeypatch):
    monkeypatch.setenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", "on")
    monkeypatch.setenv("BEN_WORKSPACE_CHUNK_RETRIEVAL_WORKSPACE_IDS", str(WS_A))
    image = _row(rid=IMAGE_ID, text="", name="scan.png", index_status="not_indexed")
    ready = ready_file_from_row(image, ORG_A, WS_A)
    assert ready is not None
    _patch_session(monkeypatch, [image])
    out = await load_ready_files_context(
        ORG_A, WS_A, max_chars=10_000, user_query="Describe the uploaded image scan.png"
    )
    assert out.block == ""
    assert out.used_files == ()
    assert out.count == 0
    assert "scan.png" not in (out.block or "")


def _patch_stream_pipeline(monkeypatch, captured):
    async def fake_stream(message, tenant_id, tier, *, provider_id=None, model_override=None, system=None):
        captured["message"] = message
        yield ("ok", "model-x", "openai")

    async def _aid(*a, **k):
        return None

    monkeypatch.setattr("services.chat_service.resolve_thread_id", lambda *a, **k: _aid())
    monkeypatch.setattr("services.chat_service.is_project_setup_thread", lambda _tid: False)

    async def _ctx(_o, _t, m):
        return m

    async def _knowledge(_m, payload):
        return payload

    monkeypatch.setattr("services.chat_service.build_chat_message_with_thread_context", _ctx)
    monkeypatch.setattr("services.chat_service.inject_knowledge_few_shot", _knowledge)
    async def _copilot(*_a, **_k):
        return []

    monkeypatch.setattr("services.chat_service.route_request_stream", fake_stream)
    monkeypatch.setattr("services.chat_service.run_copilot_preamble", _copilot)
    monkeypatch.setattr("services.chat_service.persist_chat_exchange_sqlite", lambda *a, **k: (1, 2))
    monkeypatch.setattr("services.chat_service._schedule_chat_persist", lambda *_a, **_k: None)


@pytest.mark.asyncio
async def test_chat_does_not_claim_image_as_text_evidence(monkeypatch):
    captured: dict = {}
    _patch_stream_pipeline(monkeypatch, captured)
    image = _row(rid=IMAGE_ID, text="", name="scan.png")
    _patch_session(monkeypatch, [image])

    events = []
    async for line in chat_service.stream_chat_response(
        "What is in the uploaded image?",
        "user-1",
        str(ORG_A),
        "free",
        thread_id=uuid.uuid4(),
        provider_id="gpt",
        project_id=WS_A,
    ):
        events.append(json.loads(line))
    done = next(e for e in events if e["type"] == "done")
    assert done["workspace_files_injected"] is False
    assert done["workspace_files_used"] == []
    assert "<workspace_files>" not in captured["message"]
    assert "scan.png" not in captured["message"]


@pytest.mark.asyncio
async def test_text_file_still_ready_and_retrievable(monkeypatch):
    monkeypatch.delenv("BEN_WORKSPACE_CHUNK_RETRIEVAL", raising=False)
    text = _row(
        rid=TEXT_ID,
        text="The verified opening width is 1370 mm.",
        name="F0-test.txt",
        media_type="text/plain",
        extraction_status="complete",
        index_status="not_indexed",
    )
    _patch_session(monkeypatch, [text])
    out = await load_ready_files_context(
        ORG_A, WS_A, max_chars=10_000, user_query="What is the verified opening width?"
    )
    assert out.count == 1
    assert out.used_files == ({"id": str(TEXT_ID), "name": "F0-test.txt"},)
    assert "1370" in out.block


def test_broken_source_projection_still_fails():
    empty = _assemble_document(
        [("", False, None)], source_page_count=1, parser_id="generic_text", parser_version="1"
    )
    status, _text, code, _msg = _legacy_projection(empty, "failed")
    assert status == "failed" and code == "extraction_failed"
    failed_page = _assemble_document(
        [(None, False, "extract_error:ValueError:boom")],
        source_page_count=1,
        parser_id="generic_text",
        parser_version="1",
    )
    status, _text, code, _msg = _legacy_projection(failed_page, "failed")
    assert status == "failed" and code == "extraction_failed"
    zero = _assemble_document(
        [], source_page_count=0, parser_id="pypdf", parser_version="1",
        warnings=("pdf_open_failed:PdfReadError",),
    )
    status, _text, code, _msg = _legacy_projection(zero, "failed")
    assert status == "failed" and code == "extraction_failed"


def test_pipeline_exception_path_still_marks_source_failed():
    """Parser/infra exceptions keep using extraction_error, not the no-text ready path."""
    from services.workspace_files import extraction_pipeline as pipe

    src = inspect.getsource(pipe.run_structured_extraction)
    assert 'failure_code="extraction_error"' in src
    assert 'status="failed"' in src
    assert "valid_source_without_text" in src
