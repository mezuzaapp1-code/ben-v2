"""Lightweight runtime timing (logs only; never blocks or shares cross-subsystem state)."""
from __future__ import annotations

import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from services.ops.structured_log import log_info, log_warning


@asynccontextmanager
async def measure(
    *,
    subsystem: str,
    operation: str,
    provider: str | None = None,
    **extra: Any,
) -> AsyncIterator[None]:
    """Record duration_ms on exit. Re-raises exceptions after logging outcome=error."""
    t0 = time.perf_counter()
    outcome = "ok"
    category: str | None = None
    exc: BaseException | None = None
    try:
        yield
    except BaseException as e:
        outcome = "error"
        exc = e
        from services.ops.failure_classification import classify_failure

        category = classify_failure(e)
        raise
    finally:
        duration_ms = int((time.perf_counter() - t0) * 1000)
        if outcome == "error" and category == "timeout":
            log_warning(
                f"{operation} timed out",
                subsystem=subsystem,
                provider=provider,
                category="timeout",
                duration_ms=duration_ms,
                operation=operation,
                outcome="timeout",
                **extra,
            )
        elif outcome == "error":
            log_warning(
                f"{operation} failed",
                subsystem=subsystem,
                provider=provider,
                category=category,
                exc=exc,
                duration_ms=duration_ms,
                operation=operation,
                **extra,
            )
        else:
            log_info(
                f"{operation} completed",
                subsystem=subsystem,
                provider=provider,
                duration_ms=duration_ms,
                outcome=outcome,
                operation=operation,
                **extra,
            )


def log_timing(
    message: str,
    *,
    subsystem: str,
    operation: str,
    duration_ms: int,
    outcome: str = "ok",
    provider: str | None = None,
    category: str | None = None,
    **extra: Any,
) -> None:
    """Explicit timing log (degraded, provider complete, council total, etc.)."""
    if outcome in ("degraded", "timeout", "error"):
        log_warning(
            message,
            subsystem=subsystem,
            provider=provider,
            category=category or outcome,
            duration_ms=duration_ms,
            operation=operation,
            outcome=outcome,
            **extra,
        )
    else:
        log_info(
            message,
            subsystem=subsystem,
            provider=provider,
            duration_ms=duration_ms,
            operation=operation,
            outcome=outcome,
            **extra,
        )
