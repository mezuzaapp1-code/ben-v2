"""N3.0 acquisition runtime contracts — frozen field names and enums."""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

AcquisitionStage = Literal[
    "load_source",
    "fetch",
    "parse",
    "normalize",
    "persist",
    "complete",
]

CollectStatus = Literal[
    "succeeded",
    "failed",
    "rejected",
]

AcquisitionErrorClass = Literal[
    "source_not_found",
    "source_disabled",
    "invalid_feed_url",
    "ssrf_blocked",
    "dns_blocked",
    "redirect_blocked",
    "timeout",
    "http_error",
    "response_too_large",
    "unsupported_content_type",
    "parse_error",
    "normalize_error",
    "persist_error",
    "concurrency_conflict",
    "internal_error",
]

GuidSource = Literal["feed_guid", "link", "hash"]

ACQUISITION_MAX_REDIRECTS = 3
ACQUISITION_CONNECT_TIMEOUT_S = 5.0
ACQUISITION_TOTAL_TIMEOUT_S = 15.0
ACQUISITION_MAX_BODY_BYTES = 5_242_880
ACQUISITION_USER_AGENT = "BEN-NewsCollector/0.1"
ACQUISITION_SUMMARY_MAX_CHARS = 4000
ACQUISITION_COLLECT_BUDGET_S = 30.0

_ACQUISITION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)


def new_acquisition_id() -> str:
    return str(uuid.uuid4())


def validate_acquisition_id(value: str) -> bool:
    return bool(_ACQUISITION_ID_RE.match(value or ""))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class AcquisitionError:
    acquisition_id: str
    stage: AcquisitionStage
    error_class: AcquisitionErrorClass
    message: str
    retryable: bool = False
    http_status: int | None = None
    details: dict[str, str | int | bool | None] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "acquisition_id": self.acquisition_id,
            "stage": self.stage,
            "error_class": self.error_class,
            "message": (self.message or "")[:500],
            "retryable": self.retryable,
            "http_status": self.http_status,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class AcquisitionContext:
    acquisition_id: str
    source_id: uuid.UUID
    source_name: str
    feed_url: str
    category: str
    language: str
    enabled: bool
    started_at: datetime
    request_id: str | None = None
    adapter_name: str = "rss_atom"
    user_agent: str = ACQUISITION_USER_AGENT


@dataclass(frozen=True, slots=True)
class FetchResult:
    acquisition_id: str
    ok: bool
    requested_url: str
    final_url: str | None
    status_code: int | None
    content_type: str | None
    body: bytes | None
    body_size: int
    redirect_count: int = 0
    elapsed_ms: int = 0
    error: AcquisitionError | None = None
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True, slots=True)
class NormalizedItem:
    acquisition_id: str
    source_id: uuid.UUID
    guid: str
    canonical_url: str
    title: str
    category: str
    summary: str | None = None
    image_url: str | None = None
    published_at: datetime | None = None
    guid_source: GuidSource = "feed_guid"


@dataclass(frozen=True, slots=True)
class PersistResult:
    acquisition_id: str
    source_id: uuid.UUID
    attempted_count: int
    inserted_count: int
    skipped_count: int
    failed_count: int
    error: AcquisitionError | None = None


@dataclass(frozen=True, slots=True)
class CollectResult:
    acquisition_id: str
    source_id: uuid.UUID
    status: CollectStatus
    adapter_name: str
    started_at: datetime
    finished_at: datetime
    stage_reached: AcquisitionStage
    fetched_bytes: int = 0
    http_status: int | None = None
    final_url: str | None = None
    parsed_count: int = 0
    normalized_count: int = 0
    inserted_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    error: AcquisitionError | None = None
    request_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "acquisition_id": self.acquisition_id,
            "source_id": str(self.source_id),
            "status": self.status,
            "adapter_name": self.adapter_name,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "stage_reached": self.stage_reached,
            "fetched_bytes": self.fetched_bytes,
            "http_status": self.http_status,
            "final_url": self.final_url,
            "parsed_count": self.parsed_count,
            "normalized_count": self.normalized_count,
            "inserted_count": self.inserted_count,
            "skipped_count": self.skipped_count,
            "failed_count": self.failed_count,
            "error": self.error.to_dict() if self.error else None,
            "request_id": self.request_id,
        }


def make_error(
    acquisition_id: str,
    *,
    stage: AcquisitionStage,
    error_class: AcquisitionErrorClass,
    message: str,
    retryable: bool = False,
    http_status: int | None = None,
    details: dict[str, str | int | bool | None] | None = None,
) -> AcquisitionError:
    return AcquisitionError(
        acquisition_id=acquisition_id,
        stage=stage,
        error_class=error_class,
        message=(message or "")[:500],
        retryable=retryable,
        http_status=http_status,
        details=details,
    )
