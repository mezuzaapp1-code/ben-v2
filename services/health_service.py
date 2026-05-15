"""Operational health and readiness probes (no provider API calls)."""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from database.connection import get_db_session

logger = logging.getLogger(__name__)

SERVICE_NAME = "ben-v2"
DB_PING_TIMEOUT_S = 2.0
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
    synthesis_ok = bool(
        _env_present("SYNTHESIS_MODEL") or openai_ok
    )  # synthesis uses OpenAI; model has code default
    return {
        "openai_configured": openai_ok,
        "anthropic_configured": anthropic_ok,
        "synthesis_model_configured": synthesis_ok,
    }


def _required_env_ready() -> bool:
    return _env_present("DATABASE_URL") and _env_present("OPENAI_API_KEY") and _env_present("ANTHROPIC_API_KEY")


async def ping_database() -> bool:
    try:
        async with asyncio.timeout(DB_PING_TIMEOUT_S):
            async with get_db_session() as session:
                await session.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.warning("health: database unavailable: %s", e)
        return False


async def get_migration_head() -> str | None:
    try:
        async with asyncio.timeout(DB_PING_TIMEOUT_S):
            async with get_db_session() as session:
                row = (await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))).scalar_one_or_none()
                if row is None:
                    return None
                return str(row).strip() or None
    except Exception as e:
        logger.warning("health: migration lookup failed: %s", e)
        return None


async def build_health_payload() -> tuple[dict[str, Any], int]:
    checks_env = env_checks()
    if not checks_env["openai_configured"]:
        logger.warning("health: OPENAI_API_KEY not configured")
    if not checks_env["anthropic_configured"]:
        logger.warning("health: ANTHROPIC_API_KEY not configured")
    if not checks_env["synthesis_model_configured"]:
        logger.warning("health: synthesis not configured (no OPENAI_API_KEY or SYNTHESIS_MODEL)")

    db_ok = await ping_database()
    payload = {
        "status": "healthy" if db_ok else "degraded",
        "service": SERVICE_NAME,
        "version": get_version(),
        "timestamp": _utc_now_iso(),
        "checks": {
            "database": "ok" if db_ok else "fail",
            **checks_env,
        },
    }
    return payload, 200 if db_ok else 503


async def build_ready_payload() -> tuple[dict[str, Any], int]:
    if not _env_present("DATABASE_URL"):
        logger.warning("ready: DATABASE_URL not configured")
    if not _env_present("OPENAI_API_KEY"):
        logger.warning("ready: OPENAI_API_KEY not configured")
    if not _env_present("ANTHROPIC_API_KEY"):
        logger.warning("ready: ANTHROPIC_API_KEY not configured")

    db_ok = await ping_database()
    migration_head = await get_migration_head() if db_ok else None
    head = migration_head if migration_head else "unknown"
    env_ok = _required_env_ready()
    ready = db_ok and env_ok and migration_head is not None

    payload = {
        "status": "ready" if ready else "not_ready",
        "migration_head": head,
        "ready": ready,
    }
    return payload, 200 if ready else 503
