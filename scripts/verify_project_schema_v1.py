"""Verify project management tables and RLS policies exist."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
load_dotenv(_ROOT / ".env")

TABLES = ("financial_ledger", "project_members", "project_tasks", "projects")


async def main() -> int:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("DATABASE_URL missing")
        return 1

    eng = create_async_engine(url)
    failures: list[str] = []
    async with eng.connect() as conn:
        for table in TABLES:
            row = await conn.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'ben' AND table_name = :t
                    """
                ),
                {"t": table},
            )
            if row.scalar() != 1:
                failures.append(f"missing table ben.{table}")

            pol = await conn.execute(
                text(
                    """
                    SELECT policyname FROM pg_policies
                    WHERE schemaname = 'ben' AND tablename = :t
                    """
                ),
                {"t": table},
            )
            names = [r[0] for r in pol.fetchall()]
            if "tenant_isolation" not in names:
                failures.append(f"missing tenant_isolation policy on ben.{table}")

        ver = await conn.execute(text("SELECT version_num FROM alembic_version"))
        head = ver.scalar()
        if head != "005_project_management_v1":
            failures.append(f"unexpected alembic head: {head}")

    await eng.dispose()

    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1

    print("project_schema_verify=PASS")
    print("tables:", list(TABLES))
    print("alembic_head:", "005_project_management_v1")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
