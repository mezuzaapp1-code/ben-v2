"""Persist closed-beta auditor feedback to tasks/feedback/ (Task 010)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.ops.structured_log import log_info, log_warning

_ROOT = Path(__file__).resolve().parents[1]
FEEDBACK_DIR = _ROOT / "tasks" / "feedback"
_ALIAS_FILE_RE = re.compile(r"[^a-zA-Z0-9_-]+")


def _safe_alias_token(alias: str) -> str:
    token = _ALIAS_FILE_RE.sub("_", alias.strip().lower()).strip("_")
    return token[:48] or "auditor"


def _timestamp_token() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


async def capture_beta_feedback(
    *,
    message: str,
    tester_alias: str,
    org_id: str,
    theme: str = "dark",
    project_name: str = "",
    route: str = "/chat/stream",
    extra: dict[str, Any] | None = None,
) -> Path | None:
    """Write feedback JSON to tasks/feedback/feedback_{alias}_{timestamp}.json."""
    text = (message or "").strip()
    if not text:
        return None

    FEEDBACK_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"feedback_{_safe_alias_token(tester_alias)}_{_timestamp_token()}.json"
    path = FEEDBACK_DIR / filename

    payload = {
        "tester_alias": tester_alias,
        "org_id": org_id,
        "project_name": project_name or None,
        "theme": theme,
        "message": text,
        "route": route,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        **(extra or {}),
    }

    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        log_info(
            "beta feedback captured",
            subsystem="beta",
            operation="feedback_capture",
            alias=tester_alias,
            path=str(path),
        )
        return path
    except OSError as exc:
        log_warning(
            "beta feedback capture failed",
            subsystem="beta",
            operation="feedback_capture",
            category="io_error",
            exc=exc,
        )
        return None
