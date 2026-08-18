"""Truthful processing_stage contract for workspace file payloads."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

from services.workspace_files.lifecycle import (
    derive_processing_stage,
    derive_processing_stage_from_fields,
    page_progress_from_fields,
)
from services.workspace_files.service import _payload

ORG = UUID("11111111-1111-1111-1111-111111111111")
WS = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
FID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")


def test_queued_upload_is_queued_not_ready():
    assert (
        derive_processing_stage_from_fields(
            status="queued", extraction_status="pending", index_status="not_indexed"
        )
        == "queued"
    )
    assert (
        derive_processing_stage_from_fields(status="uploaded", extraction_status="pending")
        == "queued"
    )


def test_extracting_precedes_indexing():
    assert (
        derive_processing_stage_from_fields(
            status="queued", extraction_status="extracting", index_status="not_indexed"
        )
        == "extracting"
    )
    assert (
        derive_processing_stage_from_fields(
            status="queued", extraction_status="extracting", index_status="indexing"
        )
        == "extracting"
    )


def test_indexing_after_parse_before_ready():
    assert (
        derive_processing_stage_from_fields(
            status="queued", extraction_status="complete", index_status="indexing"
        )
        == "indexing"
    )
    assert (
        derive_processing_stage_from_fields(
            status="processing", extraction_status="complete", index_status="not_indexed"
        )
        == "indexing"
    )


def test_ready_only_when_status_is_ready():
    assert (
        derive_processing_stage_from_fields(
            status="ready", extraction_status="complete", index_status="indexed"
        )
        == "ready"
    )
    # Never invent READY from extraction/index flags while status is still queued.
    assert (
        derive_processing_stage_from_fields(
            status="queued", extraction_status="complete", index_status="indexed"
        )
        == "queued"
    )


def test_failed_is_visible():
    assert derive_processing_stage_from_fields(status="failed") == "failed"
    assert (
        derive_processing_stage_from_fields(
            status="queued", extraction_status="failed", index_status="not_indexed"
        )
        == "failed"
    )


def test_page_progress_requires_both_real_counts():
    assert page_progress_from_fields(page_count=20, pages_extracted=3) == (3, 20)
    assert page_progress_from_fields(page_count=20, pages_extracted=None) is None
    assert page_progress_from_fields(page_count=None, pages_extracted=3) is None
    assert page_progress_from_fields(page_count=0, pages_extracted=0) is None


def _row(**overrides):
    base = dict(
        id=FID,
        org_id=ORG,
        workspace_id=WS,
        project_id=WS,
        original_filename="TLV062_1 (1).PDF",
        display_name="TLV062_1 (1).PDF",
        media_type="application/pdf",
        byte_size=29438410,
        checksum="abc",
        status="queued",
        uploaded_by="user",
        source_chat_id=None,
        failure_code=None,
        failure_message=None,
        created_at=datetime(2026, 8, 18, 13, 6, 27, tzinfo=timezone.utc),
        updated_at=datetime(2026, 8, 18, 13, 6, 27, tzinfo=timezone.utc),
        extracted_text=None,
        extraction_status="pending",
        index_status="not_indexed",
        page_count=None,
        indexed_chunk_count=None,
        indexed_at=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_payload_exposes_lifecycle_fields_and_stage():
    payload = _payload(_row(extraction_status="extracting", index_status="not_indexed"))
    assert payload["status"] == "queued"
    assert payload["extraction_status"] == "extracting"
    assert payload["index_status"] == "not_indexed"
    assert payload["processing_stage"] == "extracting"
    assert payload["processing_stage"] != "ready"
    assert "percent" not in payload
    assert "processing_percent" not in payload


def test_payload_ready_stage_matches_status():
    payload = _payload(
        _row(
            status="ready",
            extraction_status="complete",
            index_status="indexed",
            page_count=20,
            indexed_chunk_count=128,
        )
    )
    assert payload["processing_stage"] == "ready"
    assert derive_processing_stage(_row(status="ready", extraction_status="complete", index_status="indexed")) == "ready"
