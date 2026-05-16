"""Structured runtime diagnostics: safe fields only, no prompts/secrets/PII."""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

from auth.tenant_binding import TenantContext
from services.ops.request_context import get_request_id
from services.ops.runtime_events import (
    COUNCIL_COMPLETED,
    COUNCIL_PENDING,
    COUNCIL_RUNNING,
    COUNCIL_STARTED,
    OVERLOAD_REJECTED,
    PERSISTENCE_FAILED,
    PROVIDER_TIMEOUT,
    REQUEST_COMPLETED,
    REQUEST_FAILED,
    REQUEST_STARTED,
    RUNTIME_SNAPSHOT,
    IDEMPOTENCY_REJECTED,
    REPLAY_DETECTED,
    STALE_RUNTIME_STATE_RECOVERED,
    PERSISTENCE_RECOVERY,
)
from services.ops.structured_log import log_info, log_warning

ProviderOutcome = Literal["ok", "timeout", "degraded", "error"]

_HEBREW_RE = re.compile(r"[\u0590-\u05FF]")
_ARABIC_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F]")
_LATIN_RE = re.compile(r"[A-Za-z]")

_FORBIDDEN_FIELD_KEYS = frozenset(
    {
        "message",
        "question",
        "content",
        "prompt",
        "response",
        "text",
        "authorization",
        "jwt",
        "email",
        "user_id",
        "tenant_id",
        "org_id",
    }
)


def anonymize_tenant_id(tenant_id: str) -> str:
    raw = (tenant_id or "").strip()
    if not raw:
        return "unknown"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def detect_dominant_language(text: str) -> str:
    """Lightweight script hint for diagnostics (not full language contract)."""
    t = (text or "").strip()
    if not t:
        return "en"
    he = len(_HEBREW_RE.findall(t))
    ar = len(_ARABIC_RE.findall(t))
    lat = len(_LATIN_RE.findall(t))
    if he >= ar and he > lat:
        return "he"
    if ar > he and ar > lat:
        return "ar"
    if he > 0 and lat > 0:
        return "mixed"
    return "en"


def _sanitize_fields(fields: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, val in fields.items():
        if key in _FORBIDDEN_FIELD_KEYS:
            continue
        if val is None:
            continue
        if isinstance(val, str) and len(val) > 256:
            continue
        out[key] = val
    return out


def emit_runtime_event(event: str, *, level: str = "info", **fields: Any) -> None:
    if event not in (
        REQUEST_STARTED,
        REQUEST_COMPLETED,
        REQUEST_FAILED,
        COUNCIL_PENDING,
        COUNCIL_RUNNING,
        COUNCIL_STARTED,
        COUNCIL_COMPLETED,
        PROVIDER_TIMEOUT,
        OVERLOAD_REJECTED,
        PERSISTENCE_FAILED,
        RUNTIME_SNAPSHOT,
        IDEMPOTENCY_REJECTED,
        REPLAY_DETECTED,
        STALE_RUNTIME_STATE_RECOVERED,
        PERSISTENCE_RECOVERY,
    ):
        return
    payload = _sanitize_fields(fields)
    payload["event"] = event
    rid = get_request_id()
    if rid:
        payload["request_id"] = rid
    operation = str(payload.pop("operation", event))
    outcome = str(payload.pop("outcome", "unknown" if level == "warning" else "ok"))
    if level == "warning":
        log_warning(
            event,
            subsystem="runtime_diagnostics",
            operation=operation,
            outcome=outcome,
            **payload,
        )
    else:
        log_info(
            event,
            subsystem="runtime_diagnostics",
            operation=operation,
            outcome=outcome,
            **payload,
        )


@dataclass
class RequestDiagnostics:
    route: str
    tenant_type: str
    tenant_hash: str
    dominant_language: str
    started_monotonic: float = field(default_factory=time.monotonic)


_request_diag: ContextVar[RequestDiagnostics | None] = ContextVar("ben_request_diag", default=None)


def get_request_diagnostics() -> RequestDiagnostics | None:
    return _request_diag.get()


def begin_request_diagnostics(
    *,
    route: str,
    ctx: TenantContext,
    text_hint: str = "",
) -> RequestDiagnostics:
    diag = RequestDiagnostics(
        route=route,
        tenant_type=ctx.tenant_type,
        tenant_hash=anonymize_tenant_id(ctx.tenant_id),
        dominant_language=detect_dominant_language(text_hint),
    )
    _request_diag.set(diag)
    emit_runtime_event(
        REQUEST_STARTED,
        route=route,
        tenant_type=ctx.tenant_type,
        tenant_hash=diag.tenant_hash,
        dominant_language=diag.dominant_language,
    )
    return diag


def complete_request_diagnostics(*, outcome: str = "ok", **extra: Any) -> None:
    diag = _request_diag.get()
    duration_ms = int((time.monotonic() - diag.started_monotonic) * 1000) if diag else None
    route = extra.pop("route", None) or (diag.route if diag else None)
    emit_runtime_event(
        REQUEST_COMPLETED,
        route=route,
        tenant_type=diag.tenant_type if diag else None,
        tenant_hash=diag.tenant_hash if diag else None,
        dominant_language=diag.dominant_language if diag else None,
        duration_ms=duration_ms,
        outcome=outcome,
        **extra,
    )
    _request_diag.set(None)


def fail_request_diagnostics(*, outcome: str = "error", category: str | None = None, **extra: Any) -> None:
    diag = _request_diag.get()
    duration_ms = int((time.monotonic() - diag.started_monotonic) * 1000) if diag else None
    route = extra.pop("route", None) or (diag.route if diag else None)
    emit_runtime_event(
        REQUEST_FAILED,
        level="warning",
        route=route,
        tenant_type=diag.tenant_type if diag else None,
        tenant_hash=diag.tenant_hash if diag else None,
        dominant_language=diag.dominant_language if diag else None,
        duration_ms=duration_ms,
        outcome=outcome,
        category=category,
        **extra,
    )
    _request_diag.set(None)


@dataclass
class RuntimeMetricsStore:
    """Process-wide counters (safe aggregates only)."""

    provider_timeout_counts: dict[str, int] = field(default_factory=lambda: {"openai": 0, "anthropic": 0, "google": 0})
    provider_error_counts: dict[str, int] = field(default_factory=lambda: {"openai": 0, "anthropic": 0, "google": 0})
    provider_degraded_counts: dict[str, int] = field(default_factory=lambda: {"openai": 0, "anthropic": 0, "google": 0})
    provider_ok_counts: dict[str, int] = field(default_factory=lambda: {"openai": 0, "anthropic": 0, "google": 0})
    provider_duration_ms_total: dict[str, int] = field(default_factory=lambda: {"openai": 0, "anthropic": 0, "google": 0})
    synthesis_duration_ms_total: int = 0
    synthesis_ok_count: int = 0
    synthesis_timeout_count: int = 0
    synthesis_error_count: int = 0
    degraded_council_count: int = 0
    council_completed_count: int = 0
    council_duration_ms_total: int = 0
    persistence_failed_count: int = 0
    overload_rejected_counts: dict[str, int] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def _norm_provider(self, provider: str) -> str:
        p = (provider or "").lower()
        if p in ("openai", "anthropic", "google"):
            return p
        if p == "gemini":
            return "google"
        return p

    async def record_provider_call(
        self,
        *,
        provider: str,
        operation: str,
        duration_ms: int,
        outcome: ProviderOutcome,
    ) -> None:
        p = self._norm_provider(provider)
        async with self._lock:
            self.provider_duration_ms_total[p] = self.provider_duration_ms_total.get(p, 0) + duration_ms
            if outcome == "ok":
                self.provider_ok_counts[p] = self.provider_ok_counts.get(p, 0) + 1
            elif outcome == "timeout":
                self.provider_timeout_counts[p] = self.provider_timeout_counts.get(p, 0) + 1
            elif outcome == "degraded":
                self.provider_degraded_counts[p] = self.provider_degraded_counts.get(p, 0) + 1
            else:
                self.provider_error_counts[p] = self.provider_error_counts.get(p, 0) + 1

        if outcome == "timeout":
            emit_runtime_event(
                PROVIDER_TIMEOUT,
                level="warning",
                provider=p,
                operation=operation,
                duration_ms=duration_ms,
                outcome="timeout",
            )

    async def record_synthesis(self, *, duration_ms: int, outcome: ProviderOutcome) -> None:
        async with self._lock:
            self.synthesis_duration_ms_total += duration_ms
            if outcome == "ok":
                self.synthesis_ok_count += 1
            elif outcome == "timeout":
                self.synthesis_timeout_count += 1
            else:
                self.synthesis_error_count += 1
        await self.record_provider_call(
            provider="openai",
            operation="synthesis",
            duration_ms=duration_ms,
            outcome=outcome,
        )

    async def record_council_completed(
        self,
        *,
        duration_ms: int,
        synthesis_outcome: str,
        experts_ok: int,
        experts_degraded: int,
        experts_timeout: int,
        experts_error: int,
    ) -> None:
        any_degraded = experts_degraded + experts_timeout + experts_error > 0 or synthesis_outcome != "ok"
        async with self._lock:
            self.council_completed_count += 1
            self.council_duration_ms_total += duration_ms
            if any_degraded:
                self.degraded_council_count += 1
        emit_runtime_event(
            COUNCIL_COMPLETED,
            council_duration_ms=duration_ms,
            synthesis_outcome=synthesis_outcome,
            experts_ok=experts_ok,
            experts_degraded=experts_degraded,
            experts_timeout=experts_timeout,
            experts_error=experts_error,
            outcome="degraded" if any_degraded else "ok",
        )

    async def record_overload_rejected(self, *, code: str, route: str) -> None:
        async with self._lock:
            self.overload_rejected_counts[code] = self.overload_rejected_counts.get(code, 0) + 1
        emit_runtime_event(
            OVERLOAD_REJECTED,
            level="warning",
            route=route,
            overload_code=code,
            outcome="rejected",
        )

    async def record_persistence_failed(self, *, operation: str, category: str | None = None) -> None:
        async with self._lock:
            self.persistence_failed_count += 1
        emit_runtime_event(
            PERSISTENCE_FAILED,
            level="warning",
            operation=operation,
            category=category,
            outcome="error",
        )

    async def snapshot_fields(self) -> dict[str, Any]:
        async with self._lock:
            store = {
                "provider_timeout_counts": dict(self.provider_timeout_counts),
                "provider_error_counts": dict(self.provider_error_counts),
                "provider_degraded_counts": dict(self.provider_degraded_counts),
                "provider_ok_counts": dict(self.provider_ok_counts),
                "provider_duration_ms_total": dict(self.provider_duration_ms_total),
                "synthesis_timeout_count": self.synthesis_timeout_count,
                "synthesis_error_count": self.synthesis_error_count,
                "synthesis_ok_count": self.synthesis_ok_count,
                "degraded_council_count": self.degraded_council_count,
                "council_completed_count": self.council_completed_count,
                "council_duration_ms_total": self.council_duration_ms_total,
                "persistence_failed_count": self.persistence_failed_count,
                "overload_rejected_counts": dict(self.overload_rejected_counts),
            }
        from services.ops.load_governance import get_load_governor

        gov = get_load_governor().metrics_snapshot()
        inflight = gov.get("active_chat_requests", 0) + gov.get("active_council_requests", 0)
        return {
            **gov,
            "inflight_total": inflight,
            **store,
        }


_metrics: RuntimeMetricsStore | None = None


def get_runtime_metrics() -> RuntimeMetricsStore:
    global _metrics
    if _metrics is None:
        _metrics = RuntimeMetricsStore()
    return _metrics


def reset_runtime_metrics_for_tests() -> RuntimeMetricsStore:
    global _metrics
    _metrics = RuntimeMetricsStore()
    return _metrics


async def build_runtime_snapshot() -> dict[str, Any]:
    fields = await get_runtime_metrics().snapshot_fields()
    snap = {
        "status": "ok",
        "request_id": get_request_id(),
        **fields,
    }
    emit_runtime_event(RUNTIME_SNAPSHOT, **{k: v for k, v in fields.items() if isinstance(v, (int, str, dict))})
    return snap


def emit_council_started() -> None:
    diag = get_request_diagnostics()
    emit_runtime_event(
        COUNCIL_STARTED,
        route="/council",
        tenant_type=diag.tenant_type if diag else None,
        tenant_hash=diag.tenant_hash if diag else None,
        dominant_language=diag.dominant_language if diag else None,
    )


async def record_provider_call(
    *,
    provider: str,
    operation: str,
    duration_ms: int,
    outcome: ProviderOutcome,
) -> None:
    await get_runtime_metrics().record_provider_call(
        provider=provider,
        operation=operation,
        duration_ms=duration_ms,
        outcome=outcome,
    )
