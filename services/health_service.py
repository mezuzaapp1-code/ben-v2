"""Operational health and readiness probes (no provider API calls)."""
from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from auth.config import auth_config_for_health
from database.connection import get_db_session
from services.ops.request_context import attach_request_id
from services.ops.structured_log import log_warning
from services.ops.timing import measure
from services.ops.timeouts import DB_PING_TIMEOUT_S, HEALTH_ROUTE_TIMEOUT_S

SERVICE_NAME = "ben-v2"
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_version() -> str:
    for key in ("RAILWAY_GIT_COMMIT_SHA", "GIT_COMMIT", "VERCEL_GIT_COMMIT_SHA"):
        v = os.getenv(key, "").strip()
        if v:
            return v[:40]
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=1,
            cwd=_REPO_ROOT,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unknown"


def _env_present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def env_checks() -> dict[str, bool]:
    openai_ok = _env_present("OPENAI_API_KEY")
    anthropic_ok = _env_present("ANTHROPIC_API_KEY")
    synthesis_ok = bool(_env_present("SYNTHESIS_MODEL") or openai_ok)
    return {
        "openai_configured": openai_ok,
        "anthropic_configured": anthropic_ok,
        "synthesis_model_configured": synthesis_ok,
    }


def _required_env_ready() -> bool:
    return _env_present("DATABASE_URL") and _env_present("OPENAI_API_KEY") and _env_present("ANTHROPIC_API_KEY")


async def ping_database() -> bool:
    try:
        async with measure(subsystem="health", operation="db_ping", provider="database"):
            async with asyncio.timeout(DB_PING_TIMEOUT_S):
                async with get_db_session() as session:
                    await session.execute(text("SELECT 1"))
        return True
    except Exception as e:
        log_warning(
            "database ping failed",
            subsystem="health",
            provider="database",
            category="provider_unavailable",
            exc=e,
            operation="db_ping",
            outcome="error",
        )
        return False


async def get_migration_head() -> str | None:
    try:
        async with measure(subsystem="ready", operation="db_migration_lookup", provider="database"):
            async with asyncio.timeout(DB_PING_TIMEOUT_S):
                async with get_db_session() as session:
                    row = (
                        await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
                    ).scalar_one_or_none()
                    if row is None:
                        return None
                    return str(row).strip() or None
    except Exception as e:
        log_warning(
            "migration lookup failed",
            subsystem="ready",
            provider="database",
            category="unknown_error",
            exc=e,
            operation="db_migration_lookup",
            outcome="error",
        )
        return None


async def build_health_payload() -> tuple[dict[str, Any], int]:
    checks_env = env_checks()
    if not checks_env["openai_configured"]:
        log_warning("OPENAI_API_KEY not configured", subsystem="health", category="config_error")
    if not checks_env["anthropic_configured"]:
        log_warning("ANTHROPIC_API_KEY not configured", subsystem="health", category="config_error")

    try:
        async with asyncio.timeout(HEALTH_ROUTE_TIMEOUT_S):
            db_ok = await ping_database()
    except TimeoutError:
        log_warning(
            "health route budget exceeded",
            subsystem="health",
            category="timeout",
            operation="GET /health",
            outcome="timeout",
        )
        db_ok = False
    payload = attach_request_id(
        {
            "status": "healthy" if db_ok else "degraded",
            "service": SERVICE_NAME,
            "version": get_version(),
            "timestamp": _utc_now_iso(),
            "checks": {
                "database": "ok" if db_ok else "fail",
                **checks_env,
                **auth_config_for_health(),
            },
        }
    )
    return payload, 200 if db_ok else 503


async def build_ready_payload() -> tuple[dict[str, Any], int]:
    if not _env_present("DATABASE_URL"):
        log_warning("DATABASE_URL not configured", subsystem="ready", category="config_error")
    if not _env_present("OPENAI_API_KEY"):
        log_warning("OPENAI_API_KEY not configured", subsystem="ready", category="config_error")
    if not _env_present("ANTHROPIC_API_KEY"):
        log_warning("ANTHROPIC_API_KEY not configured", subsystem="ready", category="config_error")

    db_ok = False
    migration_head = None
    try:
        async with asyncio.timeout(HEALTH_ROUTE_TIMEOUT_S):
            db_ok = await ping_database()
            migration_head = await get_migration_head() if db_ok else None
    except TimeoutError:
        log_warning(
            "ready route budget exceeded",
            subsystem="ready",
            category="timeout",
            operation="GET /ready",
            outcome="timeout",
        )
    head = migration_head if migration_head else "unknown"
    env_ok = _required_env_ready()
    ready = db_ok and env_ok and migration_head is not None

    payload = attach_request_id(
        {
            "status": "ready" if ready else "not_ready",
            "migration_head": head,
            "ready": ready,
            "auth": auth_config_for_health(),
        }
    )
    return payload, 200 if ready else 503
