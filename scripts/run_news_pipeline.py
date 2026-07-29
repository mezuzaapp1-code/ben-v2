#!/usr/bin/env python3
"""CLI entrypoint for BEN News seed + bounded pipeline (Railway Cron / ops).

Examples:
  python scripts/run_news_pipeline.py --seed-only
  python scripts/run_news_pipeline.py --max-sources 15
  python scripts/run_news_pipeline.py --seed --max-sources 15
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def _main() -> int:
    parser = argparse.ArgumentParser(description="BEN News seed and/or bounded pipeline run")
    parser.add_argument("--seed", action="store_true", help="Seed curated sources before pipeline")
    parser.add_argument("--seed-only", action="store_true", help="Only seed; skip pipeline")
    parser.add_argument("--max-sources", type=int, default=20)
    parser.add_argument("--lookback-hours", type=int, default=72)
    parser.add_argument("--max-articles", type=int, default=500)
    parser.add_argument("--per-source-timeout", type=float, default=45.0)
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--dry-run-build", action="store_true")
    parser.add_argument("--no-live-validate", action="store_true", help="Skip fetch_safe during seed")
    args = parser.parse_args()

    from services.ops.request_context import new_request_id
    from services.ops.structured_log import log_info

    new_request_id()


    if args.seed or args.seed_only:
        from services.news.seed_service import seed_curated_sources

        seed_result = await seed_curated_sources(validate_live=not args.no_live_validate)
        print(json.dumps({"seed": seed_result}, indent=2, default=str))
        log_info(
            "news seed cli finished",
            subsystem="news_pipeline",
            operation="cli_seed",
            outcome="ok",
            created=seed_result.get("created"),
            failed_count=seed_result.get("failed_count"),
        )
        if args.seed_only:
            failed = int(seed_result.get("failed_count") or 0)
            created = int(seed_result.get("created") or 0)
            existing = int(seed_result.get("existing") or 0)
            enabled = created + existing
            # Stop condition: fewer than 5 successful feeds when catalog was empty-ish
            if enabled < 5 and (created + existing) < 5:
                return 2
            return 0 if failed < len(seed_result.get("failed") or []) + 1 else 0

    from services.news.pipeline_service import run_news_pipeline

    result = await run_news_pipeline(
        max_sources=args.max_sources,
        lookback_hours=args.lookback_hours,
        max_articles=args.max_articles,
        per_source_timeout_s=args.per_source_timeout,
        skip_build=args.skip_build,
        dry_run_build=args.dry_run_build,
    )
    print(json.dumps({"pipeline": result}, indent=2, default=str))
    if result.get("status") == "rejected":
        return 3
    return 0


async def _shutdown() -> None:
    """Close DB engine so Railway Cron deployments terminate cleanly."""
    try:
        from database.connection import dispose_engine

        await dispose_engine()
    except Exception:
        pass


if __name__ == "__main__":
    async def _entry() -> int:
        try:
            return await _main()
        finally:
            await _shutdown()

    raise SystemExit(asyncio.run(_entry()))
