"""Lightweight JSON log formatter for ben.ops (stdlib only)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

# Fields promoted from logging `extra` onto the LogRecord.
STRUCTURED_FIELDS = (
    "subsystem",
    "operation",
    "request_id",
    "provider",
    "model",
    "duration_ms",
    "outcome",
    "category",
)

_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "password",
        "secret",
        "token",
        "database_url",
    }
)


def _is_sensitive_key(key: str) -> bool:
    k = key.lower()
    return any(s in k for s in _SENSITIVE_KEYS)


def _safe_value(key: str, value: Any) -> Any:
    if _is_sensitive_key(key):
        return None
    if isinstance(value, str) and value.startswith(("sk-", "sk_ant", "Bearer ")):
        return None
    return value


class BenOpsJsonFormatter(logging.Formatter):
    """One JSON object per line; safe fallback on serialization failure."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            return self._format_record(record)
        except Exception:
            # Never recurse into logging on failure.
            return (
                '{"level":"ERROR","subsystem":"logging",'
                '"message":"log serialization failed","outcome":"error"}'
            )

    def _format_record(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc)
            .replace(microsecond=0)
            .isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
        }
        for key in STRUCTURED_FIELDS:
            if not hasattr(record, key):
                continue
            val = getattr(record, key)
            if val is None or val == "":
                continue
            safe = _safe_value(key, val)
            if safe is None:
                continue
            payload[key] = safe

        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__

        return json.dumps(payload, default=str, ensure_ascii=False, separators=(",", ":"))
