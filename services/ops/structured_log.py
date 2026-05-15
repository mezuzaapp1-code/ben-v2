"""Structured WARNING/ERROR logs (no secrets, no full payloads)."""
from __future__ import annotations

import logging
from typing import Any

from services.ops.request_context import get_request_id

logger = logging.getLogger("ben.ops")


def _base_extra(
    *,
    subsystem: str,
    provider: str | None = None,
    category: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    out: dict[str, Any] = {"subsystem": subsystem}
    rid = get_request_id()
    if rid:
        out["request_id"] = rid
    if provider:
        out["provider"] = provider
    if category:
        out["category"] = category
    out.update(extra)
    return out


def log_warning(
    message: str,
    *,
    subsystem: str,
    provider: str | None = None,
    category: str | None = None,
    exc: BaseException | None = None,
    **extra: Any,
) -> None:
    logger.warning(
        message,
        exc_info=exc,
        extra=_base_extra(subsystem=subsystem, provider=provider, category=category, **extra),
    )


def log_error(
    message: str,
    *,
    subsystem: str,
    provider: str | None = None,
    category: str | None = None,
    exc: BaseException | None = None,
    **extra: Any,
) -> None:
    logger.error(
        message,
        exc_info=exc,
        extra=_base_extra(subsystem=subsystem, provider=provider, category=category, **extra),
    )
