"""Fail-closed configuration for the document-processing runner.

Default OFF. When enabled, the runner claims persisted runner_eligible jobs
only. CLAIM_GLOBAL never opens generic FIFO. Env allowlists are not required
for new uploads and are not used as the claim path.
"""
from __future__ import annotations

import os
import uuid

from services.ops.structured_log import log_warning

_SUBSYSTEM = "doc_processing"

RUNNER_ENABLED_ENV = "BEN_DOC_RUNNER_ENABLED"
RUNNER_FILE_IDS_ENV = "BEN_DOC_RUNNER_FILE_IDS"
RUNNER_WORKSPACE_IDS_ENV = "BEN_DOC_RUNNER_WORKSPACE_IDS"
RUNNER_CLAIM_GLOBAL_ENV = "BEN_DOC_RUNNER_CLAIM_GLOBAL"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _env_on(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUTHY


def runner_enabled() -> bool:
    return _env_on(RUNNER_ENABLED_ENV)


def claim_global_enabled() -> bool:
    return _env_on(RUNNER_CLAIM_GLOBAL_ENV)


def parse_uuid_allowlist(raw: str | None) -> list[uuid.UUID]:
    """Parse a comma/whitespace-separated UUID list. Invalid tokens are skipped."""
    if not raw or not raw.strip():
        return []
    out: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    invalid: list[str] = []
    for token in raw.replace(",", " ").split():
        try:
            uid = uuid.UUID(token)
        except ValueError:
            invalid.append(token)
            continue
        if uid not in seen:
            seen.add(uid)
            out.append(uid)
    if invalid:
        log_warning(
            "runner allowlist dropped invalid UUID tokens",
            subsystem=_SUBSYSTEM,
            operation="runner_config",
            outcome="invalid_allowlist_token",
            invalid_count=len(invalid),
        )
    return out


def runner_file_ids() -> list[uuid.UUID]:
    return parse_uuid_allowlist(os.getenv(RUNNER_FILE_IDS_ENV))


def runner_workspace_ids() -> list[uuid.UUID]:
    return parse_uuid_allowlist(os.getenv(RUNNER_WORKSPACE_IDS_ENV))


def resolve_runner_claim_policy() -> str:
    """Return the claim policy the runner will use this cycle.

    disabled  — BEN_DOC_RUNNER_ENABLED is off
    eligible  — enabled; claims persisted runner_eligible jobs only
    """
    if not runner_enabled():
        return "disabled"
    if claim_global_enabled():
        log_warning(
            "BEN_DOC_RUNNER_CLAIM_GLOBAL is ignored; runner uses persisted eligibility",
            subsystem=_SUBSYSTEM,
            operation="runner_config",
            outcome="claim_global_ignored",
        )
    return "eligible"
