"""Configure ben.ops logger only; leave uvicorn/root loggers unchanged."""
from __future__ import annotations

import logging
import sys

from services.ops.json_log_formatter import BenOpsJsonFormatter

_CONFIGURED = False


def configure_ben_ops_logging(*, level: int = logging.INFO) -> None:
    """Attach a single JSON stream handler to ben.ops; idempotent."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log = logging.getLogger("ben.ops")
    log.setLevel(level)
    log.propagate = False
    log.handlers.clear()

    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)
    handler.setFormatter(BenOpsJsonFormatter())
    log.addHandler(handler)

    _CONFIGURED = True
