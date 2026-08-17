"""Persisted ingest eligibility for automatic new-file processing.

Existing queued jobs default ineligible. New enqueue marks eligible unless the
file is in the hard historical quarantine. The runner claims only eligible jobs.
CLAIM_GLOBAL / generic FIFO is not required and is not used by the runner.
"""
from __future__ import annotations

import uuid
from typing import Any

# Production queued jobs that must never be claimed, reaped, or marked eligible.
PROTECTED_INGEST_FILE_IDS = frozenset(
    {
        uuid.UUID("43cef794-1fff-40ae-bd3c-47d9fc121518"),
        uuid.UUID("0bbd0dd0-cfd9-4ef4-a3b9-c1e96bef83a4"),
    }
)


def file_is_ingest_protected(file_id: Any) -> bool:
    try:
        return uuid.UUID(str(file_id)) in PROTECTED_INGEST_FILE_IDS
    except (TypeError, ValueError, AttributeError):
        return False


def new_job_is_runner_eligible(file_id: Any) -> bool:
    """New uploads are eligible. Protected historical files never are."""
    return not file_is_ingest_protected(file_id)
