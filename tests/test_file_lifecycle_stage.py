"""Truthful processing_stage contract reconciled with durable job status."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from services.workspace_files.lifecycle import (
    derive_processing_stage,
    derive_processing_stage_from_fields,
    job_status_by_file_id,
    page_progress_from_fields,
    pick_relevant_job_status,
)
from services.workspace_files.service import _payload

ORG = UUID("11111111-1111-1111-1111-111111111111")
ORG_B = UUID("22222222-2222-2222-2222-222222222222")
WS = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
WS_B = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
FID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
FID_B = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")


def test_queued_upload_is_queued_not_ready():
    assert (
        derive_processing_stage_from_fields(
            status="queued",
            extraction_status="pending",
            index_status="not_indexed",
            job_status="queued",
        )
        == "queued"
    )
    assert (
        derive_processing_stage_from_fields(status="uploaded", extraction_status="pending")
        == "queued"
    )


def test_extracting_only_when_job_running():
    assert (
        derive_processing_stage_from_fields(
            status="queued",
            extraction_status="extracting",
            index_status="not_indexed",
            job_status="running",
        )
        == "extracting"
    )
    assert (
        derive_processing_stage_from_fields(
            status="queued",
            extraction_status="extracting",
            index_status="not_indexed",
            job_status="queued",
        )
        == "queued"
    )


def test_indexing_shown_when_running_even_if_extraction_flag_still_extracting():
    assert (
        derive_processing_stage_from_fields(
            status="queued",
            extraction_status="extracting",
            index_status="indexing",
            job_status="running",
        )
        == "indexing"
    )
    assert (
        derive_processing_stage_from_fields(
            status="queued",
            extraction_status="complete",
            index_status="indexing",
            job_status="running",
        )
        == "indexing"
    )


def test_crash_during_extracting_requeue_is_queued():
    assert (
        derive_processing_stage_from_fields(
            status="queued",
            extraction_status="extracting",
            index_status="not_indexed",
            job_status="queued",
        )
        == "queued"
    )


def test_crash_during_indexing_requeue_is_queued():
    assert (
        derive_processing_stage_from_fields(
            status="queued",
            extraction_status="extracting",
            index_status="indexing",
            job_status="queued",
        )
        == "queued"
    )


def test_queued_retry_overrides_stale_failed_and_extracting_flags():
    assert (
        derive_processing_stage_from_fields(
            status="failed",
            extraction_status="failed",
            index_status="failed",
            job_status="queued",
        )
        == "queued"
    )
    assert (
        derive_processing_stage_from_fields(
            status="failed",
            extraction_status="extracting",
            index_status="not_indexed",
            job_status="queued",
        )
        == "queued"
    )


def test_running_retry_restores_extracting_and_indexing():
    assert (
        derive_processing_stage_from_fields(
            status="failed",
            extraction_status="extracting",
            index_status="not_indexed",
            job_status="running",
        )
        == "extracting"
    )
    assert (
        derive_processing_stage_from_fields(
            status="failed",
            extraction_status="complete",
            index_status="indexing",
            job_status="running",
        )
        == "indexing"
    )


def test_max_attempts_failed_job_is_failed():
    assert (
        derive_processing_stage_from_fields(
            status="queued",
            extraction_status="extracting",
            index_status="indexing",
            job_status="failed",
        )
        == "failed"
    )


def test_no_job_file_failed_is_failed():
    assert derive_processing_stage_from_fields(status="failed") == "failed"
    assert (
        derive_processing_stage_from_fields(
            status="queued", extraction_status="failed", index_status="not_indexed"
        )
        == "failed"
    )


def test_ready_wins_over_stale_job_and_file_flags():
    assert (
        derive_processing_stage_from_fields(
            status="ready",
            extraction_status="extracting",
            index_status="indexing",
            job_status="running",
        )
        == "ready"
    )
    assert (
        derive_processing_stage_from_fields(
            status="ready",
            extraction_status="complete",
            index_status="indexed",
            job_status="queued",
        )
        == "ready"
    )
    assert (
        derive_processing_stage_from_fields(
            status="queued",
            extraction_status="complete",
            index_status="indexed",
            job_status="succeeded",
        )
        == "queued"
    )


def test_page_progress_requires_both_real_counts():
    assert page_progress_from_fields(page_count=20, pages_extracted=3) == (3, 20)
    assert page_progress_from_fields(page_count=20, pages_extracted=None) is None
    assert page_progress_from_fields(page_count=None, pages_extracted=3) is None
    assert page_progress_from_fields(page_count=0, pages_extracted=0) is None


def _job(**overrides):
    now = datetime(2026, 8, 18, 13, 11, 38, tzinfo=timezone.utc)
    base = dict(
        file_id=FID,
        org_id=ORG,
        workspace_id=WS,
        status="queued",
        updated_at=now,
        created_at=now,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_pick_relevant_job_prefers_running_then_queued_then_latest_failed():
    earlier = datetime(2026, 8, 18, 13, 0, tzinfo=timezone.utc)
    later = datetime(2026, 8, 18, 13, 20, tzinfo=timezone.utc)
    assert pick_relevant_job_status([
        _job(status="queued"),
        _job(status="running", updated_at=earlier),
    ]) == "running"
    assert pick_relevant_job_status([
        _job(status="failed", updated_at=later),
        _job(status="queued", updated_at=earlier),
    ]) == "queued"
    assert pick_relevant_job_status([
        _job(status="failed", updated_at=earlier),
        _job(status="failed", updated_at=later),
    ]) == "failed"


def test_job_status_by_file_ignores_unscoped_rows_when_not_present():
    mapped = job_status_by_file_id([
        _job(file_id=FID, status="running"),
        _job(file_id=FID_B, org_id=ORG_B, workspace_id=WS_B, status="failed"),
    ])
    assert mapped[FID] == "running"
    assert mapped[FID_B] == "failed"


def test_job_status_query_is_org_workspace_file_scoped():
    src = Path("services/workspace_files/service.py").read_text()
    assert "DocumentProcessingJob.org_id == org_id" in src
    assert "DocumentProcessingJob.workspace_id == workspace_id" in src
    assert "DocumentProcessingJob.file_id.in_(file_ids)" in src
    body = src.split("async def _job_status_map_for_files", 1)[1].split("def _preview_kind", 1)[0]
    assert "reap_expired" not in body


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


def test_payload_exposes_job_status_and_does_not_fabricate_percent():
    payload = _payload(
        _row(extraction_status="extracting", index_status="not_indexed"),
        job_status="queued",
    )
    assert payload["status"] == "queued"
    assert payload["job_status"] == "queued"
    assert payload["extraction_status"] == "extracting"
    assert payload["processing_stage"] == "queued"
    assert payload["processing_stage"] != "ready"
    assert "percent" not in payload
    assert "processing_percent" not in payload


def test_payload_running_extracting_and_indexing():
    extracting = _payload(
        _row(extraction_status="extracting"),
        job_status="running",
    )
    assert extracting["processing_stage"] == "extracting"
    indexing = _payload(
        _row(extraction_status="extracting", index_status="indexing"),
        job_status="running",
    )
    assert indexing["processing_stage"] == "indexing"


def test_payload_ready_stage_matches_status():
    payload = _payload(
        _row(
            status="ready",
            extraction_status="complete",
            index_status="indexed",
            page_count=20,
            indexed_chunk_count=128,
        ),
        job_status="succeeded",
    )
    assert payload["processing_stage"] == "ready"
    assert payload["job_status"] == "succeeded"
    assert derive_processing_stage(
        _row(status="ready", extraction_status="complete", index_status="indexed"),
        "succeeded",
    ) == "ready"
