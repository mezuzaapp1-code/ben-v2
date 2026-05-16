"""In-process load governance: bounded concurrency, duplicate guard, overload metrics."""
from __future__ import annotations

import asyncio
import hashlib
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from fastapi import HTTPException

from services.ops.load_messages import (
    COUNCIL_BUSY,
    DUPLICATE_REQUEST,
    RETRY_LATER,
    RUNTIME_SATURATED,
    overload_detail,
    resolve_locale,
)
from services.ops.structured_log import log_info, log_warning


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        return default


@dataclass
class LoadGovernor:
    max_concurrent_chat: int = field(default_factory=lambda: _int_env("BEN_MAX_CONCURRENT_CHAT", 8))
    max_concurrent_council: int = field(default_factory=lambda: _int_env("BEN_MAX_CONCURRENT_COUNCIL", 2))
    max_total_inflight: int = field(default_factory=lambda: _int_env("BEN_MAX_TOTAL_INFLIGHT", 12))
    council_dedup_window_s: float = field(
        default_factory=lambda: float(_int_env("BEN_COUNCIL_DEDUP_WINDOW_S", 45))
    )

    chat_active: int = 0
    council_active: int = 0
    rejected_overload_requests: int = 0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _council_in_flight: dict[str, float] = field(default_factory=dict, repr=False)

    @property
    def total_active(self) -> int:
        return self.chat_active + self.council_active

    def metrics_snapshot(self) -> dict[str, int]:
        return {
            "active_chat_requests": self.chat_active,
            "active_council_requests": self.council_active,
            "rejected_overload_requests": self.rejected_overload_requests,
        }

    def _purge_council_keys(self, now: float) -> None:
        expired = [k for k, started in self._council_in_flight.items() if now - started > self.council_dedup_window_s]
        for k in expired:
            self._council_in_flight.pop(k, None)

    @staticmethod
    def council_dedup_key(tenant_id: str, question: str) -> str:
        normalized = " ".join((question or "").strip().lower().split())
        digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
        return f"council:{tenant_id}:{digest}"

    async def _reject(self, *, code: str, locale: str, status_code: int, operation: str) -> None:
        self.rejected_overload_requests += 1
        snap = self.metrics_snapshot()
        log_warning(
            "load governance rejected request",
            subsystem="load_governance",
            category=code,
            operation=operation,
            outcome="rejected",
            **snap,
        )
        raise HTTPException(
            status_code=status_code,
            detail=overload_detail(code, locale),
        )

    async def _try_total_cap(self, locale: str, operation: str) -> None:
        if self.total_active >= self.max_total_inflight:
            await self._reject(code=RETRY_LATER, locale=locale, status_code=503, operation=operation)

    @asynccontextmanager
    async def govern_chat(self, *, locale: str) -> AsyncIterator[None]:
        await self._try_total_cap(locale, "POST /chat")
        acquired = False
        async with self._lock:
            if self.chat_active >= self.max_concurrent_chat:
                self.rejected_overload_requests += 1
                snap = self.metrics_snapshot()
                log_warning(
                    "chat concurrency saturated",
                    subsystem="load_governance",
                    category=RUNTIME_SATURATED,
                    operation="POST /chat",
                    outcome="rejected",
                    **snap,
                )
                raise HTTPException(
                    status_code=503,
                    detail=overload_detail(RUNTIME_SATURATED, locale),
                )
            self.chat_active += 1
            acquired = True
        log_info(
            "chat request active",
            subsystem="load_governance",
            operation="POST /chat",
            outcome="active",
            **self.metrics_snapshot(),
        )
        t0 = time.perf_counter()
        try:
            yield
        finally:
            if acquired:
                async with self._lock:
                    self.chat_active = max(0, self.chat_active - 1)
                duration_ms = int((time.perf_counter() - t0) * 1000)
                log_info(
                    "chat request completed",
                    subsystem="load_governance",
                    operation="POST /chat",
                    outcome="completed",
                    duration_ms=duration_ms,
                    **self.metrics_snapshot(),
                )

    @asynccontextmanager
    async def govern_council(
        self,
        *,
        tenant_id: str,
        question: str,
        locale: str,
    ) -> AsyncIterator[None]:
        dedup_key = self.council_dedup_key(tenant_id, question)
        now = time.monotonic()
        async with self._lock:
            self._purge_council_keys(now)
            if dedup_key in self._council_in_flight:
                self.rejected_overload_requests += 1
                snap = self.metrics_snapshot()
                log_warning(
                    "duplicate council request blocked",
                    subsystem="load_governance",
                    category=DUPLICATE_REQUEST,
                    operation="POST /council",
                    outcome="rejected",
                    **snap,
                )
                raise HTTPException(
                    status_code=429,
                    detail=overload_detail(DUPLICATE_REQUEST, locale),
                )

        await self._try_total_cap(locale, "POST /council")

        acquired = False
        async with self._lock:
            if self.council_active >= self.max_concurrent_council:
                self.rejected_overload_requests += 1
                snap = self.metrics_snapshot()
                log_warning(
                    "council concurrency saturated",
                    subsystem="load_governance",
                    category=COUNCIL_BUSY,
                    operation="POST /council",
                    outcome="rejected",
                    **snap,
                )
                raise HTTPException(
                    status_code=503,
                    detail=overload_detail(COUNCIL_BUSY, locale),
                )
            self.council_active += 1
            self._council_in_flight[dedup_key] = now
            acquired = True

        log_info(
            "council request active",
            subsystem="load_governance",
            operation="POST /council",
            outcome="active",
            **self.metrics_snapshot(),
        )
        t0 = time.perf_counter()
        try:
            yield
        finally:
            if acquired:
                async with self._lock:
                    self.council_active = max(0, self.council_active - 1)
                    self._council_in_flight.pop(dedup_key, None)
                duration_ms = int((time.perf_counter() - t0) * 1000)
                log_info(
                    "council request completed",
                    subsystem="load_governance",
                    operation="POST /council",
                    outcome="completed",
                    council_duration_ms=duration_ms,
                    **self.metrics_snapshot(),
                )


_governor: LoadGovernor | None = None


def get_load_governor() -> LoadGovernor:
    global _governor
    if _governor is None:
        _governor = LoadGovernor()
    return _governor


def reset_load_governor_for_tests(
    *,
    max_chat: int = 8,
    max_council: int = 2,
    max_total: int = 12,
    dedup_window_s: float = 45.0,
) -> LoadGovernor:
    """Replace global governor (tests only)."""
    global _governor
    _governor = LoadGovernor(
        max_concurrent_chat=max_chat,
        max_concurrent_council=max_council,
        max_total_inflight=max_total,
        council_dedup_window_s=dedup_window_s,
    )
    return _governor


def locale_for_request(request: Any, text: str) -> str:
    accept = None
    if request is not None:
        accept = request.headers.get("Accept-Language")
    return resolve_locale(accept_language=accept, text=text)
