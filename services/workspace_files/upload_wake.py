"""Best-effort post-commit wake for a just-uploaded WorkspaceFile.

Does not extract inside the upload HTTP request. Does not claim the global
FIFO. Schedules drain_document_processing_job_for_file for THIS file_id only.
Fail-closed OFF. Process-local concurrency cap; a full cap skips the wake and
leaves the durable job for cron recovery.
"""
from __future__ import annotations

import asyncio
import os
import threading
import uuid
from typing import Any

from services.ops.structured_log import log_info, log_warning
from services.workspace_files.ingest_eligibility import (
    file_is_ingest_protected,
    new_job_is_runner_eligible,
)
from services.workspace_files.runner_config import runner_enabled

_SUBSYSTEM = "doc_processing"

UPLOAD_WAKE_ENABLED_ENV = "BEN_DOC_UPLOAD_WAKE_ENABLED"
UPLOAD_WAKE_CONCURRENCY_ENV = "BEN_DOC_UPLOAD_WAKE_CONCURRENCY"
PROCESSING_ENABLED_ENV = "BEN_DOC_PROCESSING_ENABLED"

DEFAULT_WAKE_CONCURRENCY = 2
_TRUTHY = frozenset({"1", "true", "yes", "on"})

_admit_lock = threading.Lock()
_active = 0
_inflight: set[asyncio.Task] = set()


def _env_on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def upload_wake_enabled() -> bool:
    return _env_on(UPLOAD_WAKE_ENABLED_ENV)


def processing_enabled() -> bool:
    return _env_on(PROCESSING_ENABLED_ENV)


def wake_concurrency() -> int:
    raw = os.getenv(UPLOAD_WAKE_CONCURRENCY_ENV, "").strip()
    if not raw:
        return DEFAULT_WAKE_CONCURRENCY
    try:
        n = int(raw)
    except ValueError:
        return DEFAULT_WAKE_CONCURRENCY
    return n if n >= 1 else DEFAULT_WAKE_CONCURRENCY


def active_wake_count() -> int:
    with _admit_lock:
        return _active


def reset_upload_wake_for_tests() -> None:
    """Drop in-flight accounting. Tests must not leak slots across cases."""
    global _active
    with _admit_lock:
        _active = 0
    _inflight.clear()


def _try_admit() -> bool:
    """Take one slot without waiting. False means skip — do not create a task."""
    global _active
    limit = wake_concurrency()
    with _admit_lock:
        if _active >= limit:
            return False
        _active += 1
        return True


def _release() -> None:
    global _active
    with _admit_lock:
        _active = max(0, _active - 1)


def _wake_allowed(file_id: Any) -> str | None:
    """Return a skip reason, or None if a wake may be scheduled.

    Default-off is silent (no per-upload log). Misconfig / quarantine / capacity
    are logged by the caller.
    """
    if not processing_enabled() or not upload_wake_enabled():
        return "disabled"
    if not runner_enabled():
        return "runner_disabled"
    if file_is_ingest_protected(file_id) or not new_job_is_runner_eligible(file_id):
        return "quarantined"
    return None


async def _run_wake(file_id: uuid.UUID) -> None:
    from services.workspace_files.drain import drain_document_processing_job_for_file

    try:
        await drain_document_processing_job_for_file(
            file_id,
            worker_id=f"upload-wake-{uuid.uuid4().hex[:8]}",
        )
    except Exception as exc:  # noqa: BLE001
        log_warning(
            "upload wake drain failed",
            subsystem=_SUBSYSTEM,
            operation="upload_wake",
            outcome="error",
            file_id=str(file_id),
            error_class=type(exc).__name__,
        )
    finally:
        _release()


def schedule_upload_wake(file_id: Any) -> bool:
    """Best-effort in-process wake. Never raises. Never awaits extraction.

    Returns True only when an asyncio task was created for this file_id.
    """
    try:
        fid = uuid.UUID(str(file_id))
    except (TypeError, ValueError, AttributeError):
        return False

    try:
        reason = _wake_allowed(fid)
        if reason is not None:
            if reason != "disabled":
                log_info(
                    "upload wake skipped",
                    subsystem=_SUBSYSTEM,
                    operation="upload_wake",
                    outcome=reason,
                    file_id=str(fid),
                )
            return False
        if not _try_admit():
            log_info(
                "upload wake skipped",
                subsystem=_SUBSYSTEM,
                operation="upload_wake",
                outcome="capacity",
                file_id=str(fid),
            )
            return False
        try:
            task = asyncio.create_task(_run_wake(fid), name=f"upload-wake-{fid}")
        except Exception:
            _release()
            log_warning(
                "upload wake schedule failed",
                subsystem=_SUBSYSTEM,
                operation="upload_wake",
                outcome="error",
                file_id=str(fid),
            )
            return False
        _inflight.add(task)
        task.add_done_callback(_inflight.discard)
        log_info(
            "upload wake scheduled",
            subsystem=_SUBSYSTEM,
            operation="upload_wake",
            outcome="scheduled",
            file_id=str(fid),
        )
        return True
    except Exception as exc:  # noqa: BLE001
        log_warning(
            "upload wake schedule failed",
            subsystem=_SUBSYSTEM,
            operation="upload_wake",
            outcome="error",
            file_id=str(fid),
            error_class=type(exc).__name__,
        )
        return False


async def wait_for_inflight_wakes() -> None:
    """Test helper: wait for wakes admitted by this process."""
    pending = [t for t in list(_inflight) if not t.done()]
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
