"""Startup validation: fail fast on critical env; warn on optional."""
from __future__ import annotations

import os

from services.ops.structured_log import log_warning

CRITICAL_ENV = ("DATABASE_URL", "OPENAI_API_KEY")
OPTIONAL_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "SYNTHESIS_MODEL")


def validate_startup() -> None:
    missing_critical = [k for k in CRITICAL_ENV if not os.getenv(k, "").strip()]
    if missing_critical:
        raise RuntimeError(f"Missing critical configuration: {', '.join(missing_critical)}")

    for key in OPTIONAL_ENV:
        if not os.getenv(key, "").strip():
            log_warning(
                f"optional env not set: {key}",
                subsystem="startup",
                category="config_error",
            )
